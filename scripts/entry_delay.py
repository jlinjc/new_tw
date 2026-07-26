# -*- coding: utf-8 -*-
"""追價衰減回測：如果沒在隔日進場，晚 1~3 天進場還有多少邊際？

節目盤後播出，一般教學設定「隔日進場」；但使用者實際上可能晚幾天才看到
dashboard。本腳本比較「隔日／晚1天／晚2天／晚3天」進場的 +3日、+1週 超額，
量化「訊號的保鮮期」，結果寫入 data/entry_delay.json 供 dashboard 顯示摘要。

用法：python entry_delay.py
"""
import json
import sys

import pandas as pd

from config import DATA_DIR
from performance import BENCH, load_picks, fetch_prices, _clean

DELAYS = [0, 1, 2, 3]     # 進場延後幾個交易日
SUB_HORIZONS = {"d3": 3, "w1": 5}   # 只看短天期：延遲進場對長天期意義不大
OUT = DATA_DIR / "entry_delay.json"


def windows_at_offset(close: pd.Series, air_date: str, delay: int) -> dict | None:
    close = _clean(close)
    after = close[close.index > pd.Timestamp(air_date)]
    if len(after) <= delay:
        return None
    entry_price = float(after.iloc[delay])
    entry_ts = after.index[delay]
    ret, exit_ts = {}, {}
    for key, n in SUB_HORIZONS.items():
        idx = delay + n
        if len(after) > idx:
            ret[key] = round(float(after.iloc[idx]) / entry_price - 1, 5)
            exit_ts[key] = after.index[idx]
        else:
            ret[key] = None
            exit_ts[key] = None
    return {"ret": ret, "entry_ts": entry_ts, "exit_ts": exit_ts}


def bench_at(bench: pd.Series, entry_ts, exit_ts: dict) -> dict:
    b = _clean(bench).sort_index()
    be = b.asof(entry_ts)
    ret = {}
    for key, xt in exit_ts.items():
        if xt is None or pd.isna(be):
            ret[key] = None
            continue
        bx = b.asof(xt)
        ret[key] = round(float(bx) / float(be) - 1, 5) if not pd.isna(bx) else None
    return ret


# 已驗證有短線持續性的老師（見 walk_forward.py 結果）；全體平均訊號太薄
# (~0)，看不出衰減，這批人的邊際才有意義追蹤是否隨延遲消失。
CORE_TEACHERS = {"容逸燊", "李永年", "張林忠", "鍾國忠", "黃豐凱"}


def run_cohort(dated, prices, bench, label_key):
    by_delay = {d: {h: [] for h in SUB_HORIZONS} for d in DELAYS}
    for p in dated:
        sym = p["symbol"]
        if sym not in prices or bench is None:
            continue
        sign = -1 if p["stance"] == "看空" else 1
        for d in DELAYS:
            w = windows_at_offset(prices[sym], p["date"], d)
            if not w:
                continue
            b = bench_at(bench, w["entry_ts"], w["exit_ts"])
            for h in SUB_HORIZONS:
                r, bh = w["ret"].get(h), b.get(h)
                if r is None or bh is None:
                    continue
                by_delay[d][h].append(sign * (r - bh))
    result = {}
    print(f"\n=== {label_key} ===")
    for d in DELAYS:
        row = {}
        cells = []
        for h in SUB_HORIZONS:
            exs = by_delay[d][h]
            n = len(exs)
            avg = sum(exs) / n if n else None
            row[h] = {"n": n, "avg_excess": round(avg, 5) if avg is not None else None}
            cells.append(f"{avg*100:>+6.1f}%(n={n})" if avg is not None else f"{'–':>12}")
        result[f"delay{d}"] = row
        label = "隔日進場" if d == 0 else f"晚{d}天進場"
        print(f"{label:<10}" + "".join(f"{c:>16}" for c in cells))
    return result


def main():
    picks = load_picks()
    dated = [p for p in picks if p["symbol"] and p["stance"] in ("看多", "看空")]
    symbols = sorted({p["symbol"] for p in dated} | {BENCH})
    earliest = min(p["date"] for p in dated)
    prices = fetch_prices(symbols, earliest)
    bench = prices.get(BENCH)
    print(f"Picks: {len(dated)}, symbols fetched: {len(prices)}/{len(symbols)}")

    result = {
        "all": run_cohort(dated, prices, bench, "全部推薦（訊號太薄，僅供對照）"),
        "core": run_cohort([p for p in dated if p["teacher"] in CORE_TEACHERS],
                            prices, bench, "短線有技術的老師（容逸燊/李永年/張林忠/鍾國忠/黃豐凱）"),
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n寫入 {OUT}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()

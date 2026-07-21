# -*- coding: utf-8 -*-
"""計算每筆推薦的後續股價表現。

進場：播出日之後第一個交易日的收盤價（節目為盤後播出）。
視窗：進場後 +3 / +5 / +10 / +21 個交易日（≈三天/一週/兩週/一個月）。
另計加權指數(^TWII)同期報酬與超額報酬；看空標的以反向報酬計分。

輸出 data/performance.json。
"""
import json
import re
import sys
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

from config import ANALYSIS_DIR, PERFORMANCE_JSON

HORIZONS = {"d3": 3, "w1": 5, "w2": 10, "m1": 21}
BENCH = "^TWII"


def yahoo_symbol(pick: dict) -> str | None:
    ticker = pick.get("ticker")
    market = pick.get("market")
    # 4-6 位數字，ETF 可帶字母尾碼（如 00632R、00991A）
    if (market in ("TW", "ETF") and ticker
            and re.fullmatch(r"\d{4,6}[A-Z]{0,2}", ticker)):
        suffix = ".TWO" if pick.get("tw_market") == "TPEX" else ".TW"
        return f"{ticker}{suffix}"
    if market == "US" and ticker and ticker.replace(".", "").isalpha():
        return ticker.upper()
    return None


def load_picks() -> list[dict]:
    picks = []
    for f in sorted(ANALYSIS_DIR.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        video = data["video"]
        for teacher in data.get("teachers", []):
            for p in teacher.get("picks", []):
                sym = yahoo_symbol(p)
                picks.append({
                    "video_id": video["id"],
                    "date": video["date"],
                    "teacher": teacher["name"],
                    "stock_name": p["stock_name"],
                    "ticker": p.get("ticker"),
                    "symbol": sym,
                    "stance": p["stance"],
                })
    return picks


def fetch_prices(symbols: list[str], start: str) -> dict[str, pd.Series]:
    """回傳 {symbol: close_series}（僅成功者）。"""
    out = {}
    for i in range(0, len(symbols), 50):
        batch = symbols[i:i + 50]
        df = yf.download(batch, start=start, auto_adjust=True,
                         progress=False, group_by="ticker")
        for sym in batch:
            try:
                s = df[sym]["Close"].dropna() if len(batch) > 1 else df["Close"].dropna()
                if isinstance(s, pd.DataFrame):
                    s = s.iloc[:, 0].dropna()
                if len(s) > 0:
                    out[sym] = s
            except (KeyError, IndexError):
                pass
    return out


def compute_windows(close: pd.Series, air_date: str) -> dict | None:
    """回傳 {entry_date, entry_price, ret: {h: float|None}}"""
    idx = close.index.tz_localize(None) if close.index.tz else close.index
    close = pd.Series(close.values, index=idx)
    after = close[close.index > pd.Timestamp(air_date)]
    if after.empty:
        return None
    entry_price = float(after.iloc[0])
    entry_date = after.index[0].date().isoformat()
    ret = {}
    for key, n in HORIZONS.items():
        if len(after) > n:
            ret[key] = round(float(after.iloc[n]) / entry_price - 1, 5)
        else:
            ret[key] = None  # 尚未到期
    return {"entry_date": entry_date, "entry_price": round(entry_price, 2),
            "ret": ret}


def main():
    picks = load_picks()
    dated = [p for p in picks if p["symbol"]]
    print(f"Picks total: {len(picks)}, with symbol: {len(dated)}")
    if not dated:
        PERFORMANCE_JSON.write_text("[]", encoding="utf-8")
        return

    earliest = min(p["date"] for p in dated)
    start = (datetime.fromisoformat(earliest) - timedelta(days=7)).date().isoformat()
    symbols = sorted({p["symbol"] for p in dated} | {BENCH})
    prices = fetch_prices(symbols, start)
    print(f"Prices fetched for {len(prices)}/{len(symbols)} symbols")

    bench = prices.get(BENCH)
    results = []
    for p in picks:
        rec = dict(p)
        rec["perf"] = None
        rec["bench"] = None
        rec["excess"] = None
        rec["hit"] = {}
        sym = p["symbol"]
        if sym and sym in prices:
            perf = compute_windows(prices[sym], p["date"])
            rec["perf"] = perf
            if perf and bench is not None:
                b = compute_windows(bench, p["date"])
                rec["bench"] = b
                if b:
                    rec["excess"] = {
                        k: (round(perf["ret"][k] - b["ret"][k], 5)
                            if perf["ret"][k] is not None and b["ret"][k] is not None
                            else None)
                        for k in HORIZONS}
            # 只有明確多空方向的推薦才計分；中性/觀望僅展示不評分
            if perf and p["stance"] in ("看多", "看空"):
                sign = -1 if p["stance"] == "看空" else 1
                rec["hit"] = {k: (sign * v > 0 if v is not None else None)
                              for k, v in perf["ret"].items()}
        results.append(rec)

    PERFORMANCE_JSON.write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    n_ok = sum(1 for r in results if r["perf"])
    print(f"Performance computed for {n_ok}/{len(results)} picks -> {PERFORMANCE_JSON}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()

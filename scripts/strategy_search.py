# -*- coding: utf-8 -*-
"""策略候選搜尋：對 scripts/strategy_candidates.md 列出的 18 個候選策略跑統計。

資料來源：data/analysis/*.json（老師/推薦原始資料）＋ data/performance.json
（已算好、已對齊大盤同期區間的 perf/excess，直接用，不重算大盤）。

方法論（見 strategy_candidates.md，不可違反，踩過的坑）：
1. 部位去重：同一集、同一標的（ticker 優先，沒有則用 stock_name）、同一方向
   （看多/看空），無論幾位老師推薦，一律先併成 1 個「部位」再統計勝率/超額。
   老師個人排行（如 rongyisheng_only）本質上一人一集一標的本就只有一筆，套用
   同一套部位表不影響結果，純粹保持程式一致、避免另開一套邏輯出錯。
2. 成本：可操作策略一律附扣成本後的淨值，ROUNDTRIP_COST = 0.0057
   （與 build_dashboard.py 一致）。
3. 樣本門檻：n < 15 一律標記 TOO_SMALL_SAMPLE，不論數字多好看。
4. 時間穩定性：用「全體集數日期」由舊到新排序、取中位數當全域切點（前半/後半
   對所有策略共用同一個切點，才能真正比較同一段大盤環境下的表現，而非各自
   抓子集合再各切一次），兩段都要出勝率＋平均超額；一正一負（且兩段樣本都
   不算太小）就老實標記 INCONSISTENT_ACROSS_HALVES。
5. 正負號：excess 欄位是原始（未依多空調整），使用時乘 sign = -1 if 看空 else 1。
6. 大盤對齊：直接用 performance.json 算好的 excess，不重算。

用法：python strategy_search.py   （在 scripts/ 目錄下執行，或用
      python scripts/strategy_search.py 從repo根目錄執行皆可）
"""
import json
import math
import sys
from collections import defaultdict
from datetime import datetime

from build_dashboard import ROUNDTRIP_COST, load_data, tag_first_mentions, wilson_ci
from config import DATA_DIR
from performance import yahoo_symbol

OUT = DATA_DIR / "strategy_search_results.json"

DIRECTIONAL = ("看多", "看空")

# 短線核心老師（見 walk_forward.py / entry_delay.py 的既有驗證）
CORE5 = {"容逸燊", "李永年", "張林忠", "鍾國忠", "黃豐凱"}

# 長期穩定跑輸大盤的老師（見 build_dashboard.compute_avoid_list 的定義精神）
EXCLUDED = {"朱家泓", "紀緯明", "高憲容", "權證小哥", "翁士峻", "蔡明翰", "林漢偉"}

MIN_N = 15          # 樣本門檻
MIN_HALF_N = 5       # 半段樣本低於此值時，一致性判斷標記「樣本太薄不足以判斷」
HORIZONS_OUT = ["d3", "m1"]   # 每個策略都輸出 d3（主）與 m1（對照）


# --------------------------------------------------------------------------
# 1. 建立「部位」表：同集＋同標的＋同方向 併成一筆，並附策略判斷所需的聚合欄位
# --------------------------------------------------------------------------
def build_positions(episodes):
    positions = []
    for ep in episodes:
        date = ep["date"]
        # 每位老師該集的計分推薦數＝精選度（話越少越可信），與 build_dashboard
        # 的 _episode_positions 定義一致
        tcount = {t["name"]: sum(1 for p in t["picks"] if p["stance"] in DIRECTIONAL)
                  for t in ep["teachers"]}
        pos = {}
        for t in ep["teachers"]:
            tname = t["name"]
            for p in t["picks"]:
                if p["stance"] not in DIRECTIONAL:
                    continue
                if not p.get("perf") or p.get("excess") is None:
                    continue
                key = (p.get("ticker") or p["stock_name"], p["stance"])
                d = pos.setdefault(key, {
                    "video_id": ep["id"], "date": date,
                    "stock_name": p["stock_name"], "ticker": p.get("ticker"),
                    "market": p.get("market"), "tw_market": p.get("tw_market"),
                    "stance": p["stance"],
                    "sign": -1 if p["stance"] == "看空" else 1,
                    "ret": p["perf"]["ret"], "excess": p["excess"],
                    "symbol": yahoo_symbol(p),
                    "teachers": set(),
                    "any_high": False, "any_medium": False,
                    "core5_teachers": set(), "core5_high": False,
                    "core5_daycounts": [], "all_daycounts": [],
                    "excluded_teachers": set(), "mention_seqs": [],
                })
                d["teachers"].add(tname)
                conf = p.get("confidence")
                if conf == "high":
                    d["any_high"] = True
                elif conf == "medium":
                    d["any_medium"] = True
                d["all_daycounts"].append(tcount[tname])
                if tname in CORE5:
                    d["core5_teachers"].add(tname)
                    d["core5_daycounts"].append(tcount[tname])
                    if conf == "high":
                        d["core5_high"] = True
                if tname in EXCLUDED:
                    d["excluded_teachers"].add(tname)
                ms = p.get("mention_seq")
                if ms is not None:
                    d["mention_seqs"].append(ms)
        for d in pos.values():
            d["n_teachers"] = len(d["teachers"])
            d["core5_flag"] = bool(d["core5_teachers"])
            d["excluded_flag"] = bool(d["excluded_teachers"])
            d["min_daycount_all"] = min(d["all_daycounts"]) if d["all_daycounts"] else None
            d["core5_min_daycount"] = (min(d["core5_daycounts"])
                                        if d["core5_daycounts"] else None)
            # 併成一個部位後，若不同老師對同一標的的「第幾次點名」不同，取
            # 最小值（只要有人是「首次」，就當這個部位帶有新鮮訊號）
            d["mention_seq"] = min(d["mention_seqs"]) if d["mention_seqs"] else None
            positions.append(d)
    return positions


def score_position(p):
    """weighted_score_top20pct 用的綜合評分：信心 + 人數共識 + core5 + 精選日。"""
    conf_score = 2 if p["any_high"] else (1 if p["any_medium"] else 0)
    core_bonus = 2 if p["core5_flag"] else 0
    selective_bonus = 1 if (p["min_daycount_all"] is not None
                             and p["min_daycount_all"] <= 5) else 0
    return conf_score + p["n_teachers"] + core_bonus + selective_bonus


def select_weighted_top20(positions):
    """依集數所在週分組，每週取分數最高的前 20%（至少 1 筆）。"""
    weeks = defaultdict(list)
    for p in positions:
        wk = datetime.fromisoformat(p["date"]).isocalendar()[:2]
        weeks[wk].append(p)
    selected = []
    for _, ps in weeks.items():
        ps_sorted = sorted(ps, key=lambda x: -score_position(x))
        k = max(1, math.ceil(len(ps_sorted) * 0.2))
        selected.extend(ps_sorted[:k])
    return selected


# --------------------------------------------------------------------------
# 2. 全域時間切點（所有策略共用，才能比較同一段大盤環境）
# --------------------------------------------------------------------------
def compute_boundary(episodes):
    dates = sorted({ep["date"] for ep in episodes})
    if not dates:
        return None, None, None
    mid = len(dates) // 2
    boundary = dates[mid]
    return boundary, dates[0], dates[-1]


def split_halves(positions, boundary):
    half1 = [p for p in positions if p["date"] < boundary]
    half2 = [p for p in positions if p["date"] >= boundary]
    return half1, half2


# --------------------------------------------------------------------------
# 3. 單一 cohort 在單一 horizon 的統計
# --------------------------------------------------------------------------
def eval_cohort(positions, horizon):
    exs = []
    for p in positions:
        ex = p["excess"].get(horizon)
        if ex is None:
            continue
        exs.append(p["sign"] * ex)
    n = len(exs)
    if n == 0:
        return {"n": 0, "beat_rate": None, "beat_ci": None,
                "beat_net_rate": None, "avg_excess_gross": None,
                "avg_excess_net": None, "top2_share_of_gain": None}
    beat = sum(1 for e in exs if e > 0)
    net = [e - ROUNDTRIP_COST for e in exs]
    beat_net = sum(1 for e in net if e > 0)
    total = sum(exs)
    # 離群值依賴度：全期表現有多少是靠最好的 2 筆撐起來的（就是先前踩過的坑
    # ——「共識策略+16.9%」拆解後幾乎全靠某天 2 筆神單）。n<2 或總和為 0 時
    # 無意義，回傳 None。份額 >0.5 代表把最好的 2 筆拿掉，結論可能完全不同。
    top2_share = None
    if n >= 2 and total != 0:
        top2 = sum(sorted(exs)[-2:])
        top2_share = round(top2 / total, 4)
    return {
        "n": n,
        "beat_rate": round(beat / n, 4),
        "beat_ci": wilson_ci(beat, n),
        "beat_net_rate": round(beat_net / n, 4),
        "avg_excess_gross": round(sum(exs) / n, 5),
        "avg_excess_net": round(sum(net) / n, 5),
        "top2_share_of_gain": top2_share,
    }


OUTLIER_SHARE_THRESHOLD = 0.5   # 最好 2 筆佔全期總和超過此比例 = 離群值撐盤


def verdict_for(full, half1, half2):
    if full["n"] < MIN_N:
        return "TOO_SMALL_SAMPLE"
    g1, g2 = half1["avg_excess_gross"], half2["avg_excess_gross"]
    thin_half = half1["n"] < MIN_HALF_N or half2["n"] < MIN_HALF_N
    if g1 is not None and g2 is not None and (g1 > 0) != (g2 > 0) and not thin_half:
        return "INCONSISTENT_ACROSS_HALVES"
    if full["avg_excess_gross"] is not None and full["avg_excess_gross"] <= 0:
        return "NEGATIVE"
    top2 = full.get("top2_share_of_gain")
    outlier_driven = (top2 is not None and full["avg_excess_gross"] > 0
                       and abs(top2) > OUTLIER_SHARE_THRESHOLD)
    if thin_half and outlier_driven:
        return "RELIABLE_CANDIDATE_BUT_HALF_THIN_AND_OUTLIER_DRIVEN"
    if outlier_driven:
        # 平均數看起來是正的，但拿掉最好 2 筆就大幅縮水甚至翻負——這正是
        # 先前「共識+16.9%」的坑：全期平均被少數神單撐起來，本質是運氣。
        return "RELIABLE_CANDIDATE_BUT_OUTLIER_DRIVEN"
    if thin_half:
        # 全期夠大，但至少一段太薄，無法真正驗證穩定性；仍照實回報數字，
        # 但不能升級為「已驗證可靠」
        return "RELIABLE_CANDIDATE_BUT_HALF_THIN"
    return "RELIABLE_CANDIDATE"


_SEVERITY = ["TOO_SMALL_SAMPLE", "INCONSISTENT_ACROSS_HALVES", "NEGATIVE",
             "RELIABLE_CANDIDATE_BUT_HALF_THIN_AND_OUTLIER_DRIVEN",
             "RELIABLE_CANDIDATE_BUT_OUTLIER_DRIVEN",
             "RELIABLE_CANDIDATE_BUT_HALF_THIN", "RELIABLE_CANDIDATE"]


def worse(v1, v2):
    return v1 if _SEVERITY.index(v1) <= _SEVERITY.index(v2) else v2


def eval_strategy(positions, boundary):
    half1, half2 = split_halves(positions, boundary)
    out = {}
    for h in HORIZONS_OUT:
        full_r = eval_cohort(positions, h)
        h1_r = eval_cohort(half1, h)
        h2_r = eval_cohort(half2, h)
        out[h] = {"full": full_r, "half1": h1_r, "half2": h2_r,
                   "verdict": verdict_for(full_r, h1_r, h2_r)}
    return out


# --------------------------------------------------------------------------
# 4. 18 個候選策略定義
# --------------------------------------------------------------------------
def define_strategies(all_positions):
    """回傳 [(name, description, positions, primary_horizon), ...]。

    primary_horizon: "d3" / "m1" / "both"（"both" 代表 d3、m1 同等重要，見
    strategy_candidates.md 對 tsmc_only、weighted_score_top20pct 的要求）。
    """
    S = []

    def add(name, desc, pred, primary):
        S.append((name, desc, [p for p in all_positions if pred(p)], primary))

    add("baseline_all", "全部有明確多空方向且有績效的部位，無篩選（對照組）",
        lambda p: True, "d3")

    add("core5_d3", "只跟短線核心老師（容逸燊/李永年/張林忠/鍾國忠/黃豐凱）",
        lambda p: p["core5_flag"], "d3")

    add("core5_selective_d3", "core5 + 該老師當集點名 ≤5 檔（精選日，以 core5 成員的當集計分數判斷）",
        lambda p: p["core5_flag"] and p["core5_min_daycount"] is not None
                  and p["core5_min_daycount"] <= 5, "d3")

    add("core5_highconf_d3", "core5 + 該筆信心 high（以 core5 成員的信心判斷）",
        lambda p: p["core5_flag"] and p["core5_high"], "d3")

    add("core5_selective_highconf_d3", "core5 + 精選日 + 高信心（三條件疊加）",
        lambda p: p["core5_flag"] and p["core5_high"]
                  and p["core5_min_daycount"] is not None
                  and p["core5_min_daycount"] <= 5, "d3")

    add("rongyisheng_only_d3", "只跟容逸燊一人",
        lambda p: "容逸燊" in p["teachers"], "d3")

    add("consensus2_d3", "同集 ≥2 位老師同看同標的同方向（部位已去重）",
        lambda p: p["n_teachers"] >= 2, "d3")

    add("consensus2_m1", "同上（≥2 位老師共識）但 m1 持有（對照組，預期較差）",
        lambda p: p["n_teachers"] >= 2, "m1")

    add("consensus_core_d3", "同集 ≥2 位老師同看，且其中至少 1 位是 core5 成員",
        lambda p: p["n_teachers"] >= 2 and p["core5_flag"], "d3")

    add("highconf_only_d3", "全部老師，只留信心 high 的部位",
        lambda p: p["any_high"], "d3")

    add("highconf_only_m1", "全部老師，只留信心 high 的部位（m1 持有）",
        lambda p: p["any_high"], "m1")

    add("avoid_excluded_d3",
        "排除長期穩定跑輸的老師（朱家泓/紀緯明/高憲容/權證小哥/翁士峻/蔡明翰/林漢偉），"
        "且不含 core5（core5 已另外驗證，此策略只看「剩下的中段班」），後的全部推薦。"
        "定義：部位的所有貢獻老師都不在 EXCLUDED 也不在 CORE5 才納入。",
        lambda p: not p["excluded_flag"] and not p["core5_flag"], "d3")

    def bull_regime_pred(p):
        r, ex = p["ret"].get("m1"), p["excess"].get("m1")
        if r is None or ex is None:
            return False
        return (r - ex) > 0   # 大盤原始報酬（未依多空調整）> 0

    add("bull_regime_only_m1",
        "只留「該部位持有窗口內大盤原始報酬 > 0」的部位（用 ret-excess 還原判斷），m1 持有。"
        "此策略的部位母體本身就限定「有 m1 資料可判斷情境」的子集。",
        bull_regime_pred, "m1")

    add("tsmc_only", "只交易 2330 台積電（不分老師），驗證先前~90%勝率是否在更大樣本下持續",
        lambda p: p["ticker"] == "2330", "both")

    add("tw_stock_only_d3", "排除 ETF 與美股/其他，只留台股個股",
        lambda p: p["market"] == "TW", "d3")

    add("first_mention_only_d3",
        "首次點名（部位內所有貢獻老師中，最小 mention_seq == 1）",
        lambda p: p["mention_seq"] == 1, "d3")

    add("repeat_only_d3",
        "重申（部位內所有貢獻老師的 mention_seq 都 > 1，即沒有任何人是首次）",
        lambda p: p["mention_seq"] is not None and p["mention_seq"] > 1, "d3")

    weighted_sel = select_weighted_top20(all_positions)
    S.append(("weighted_score_top20pct",
               "綜合評分（信心high=2/medium=1、consensus人數、core5成員+2、精選日+1）"
               "每週取分數前20%的部位", weighted_sel, "both"))

    add("short_side_only", "只看「看空」的部位（樣本小，驗證先前發現命中率較高是否成立）",
        lambda p: p["stance"] == "看空", "d3")

    add("tw_listed_only_d3", "只交易上市(.TW)，排除上櫃(.TWO)小型股",
        lambda p: p["symbol"] is not None and p["symbol"].endswith(".TW")
                  and not p["symbol"].endswith(".TWO"), "d3")

    return S


def summarize_verdict(horizons_result, primary):
    if primary in horizons_result:
        return horizons_result[primary]["verdict"]
    # primary == "both": 取 d3、m1 兩者較差（較保守）的判定
    return worse(horizons_result["d3"]["verdict"], horizons_result["m1"]["verdict"])


def main():
    episodes = load_data()
    episodes = tag_first_mentions(episodes)
    # tag_first_mentions 依日期正序重排並回傳同一批物件；重新依日期排序供顯示
    episodes_sorted = sorted(episodes, key=lambda e: e["date"])

    boundary, first_date, last_date = compute_boundary(episodes_sorted)
    all_positions = build_positions(episodes_sorted)

    strategies = define_strategies(all_positions)

    results = []
    for name, desc, positions, primary in strategies:
        horizons_result = eval_strategy(positions, boundary)
        results.append({
            "name": name,
            "description": desc,
            "primary_horizon": primary,
            "n_positions_total": len(positions),
            "horizons": horizons_result,
            "verdict": summarize_verdict(horizons_result, primary),
        })

    out = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "methodology_ref": "scripts/strategy_candidates.md",
        "roundtrip_cost": ROUNDTRIP_COST,
        "min_n_threshold": MIN_N,
        "min_half_n_threshold": MIN_HALF_N,
        "n_episodes": len(episodes_sorted),
        "n_baseline_positions": len(all_positions),
        "date_range": {"start": first_date, "end": last_date},
        "half_boundary_date": boundary,
        "half1_range": [first_date, boundary],
        "half2_range": [boundary, last_date],
        "strategies": results,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    # 終端機摘要輸出
    print(f"集數: {len(episodes_sorted)}  部位(baseline_all, 已去重): {len(all_positions)}")
    print(f"日期範圍: {first_date} ~ {last_date}  全域切點(前/後半): {boundary}")
    print()
    header = (f'{"策略":<32}{"主要期":>6}{"n(全)":>7}{"n(前)":>7}{"n(後)":>7}'
              f'{"勝率(全)":>10}{"超額毛(全)":>11}{"超額淨(全)":>11}{"top2占比":>9}  判定')
    print(header)
    print("-" * len(header))
    for r in results:
        ph = r["primary_horizon"] if r["primary_horizon"] != "both" else "d3"
        hr = r["horizons"][ph]
        f = hr["full"]
        beat = f"{f['beat_rate']*100:.0f}%" if f["beat_rate"] is not None else "–"
        exg = f"{f['avg_excess_gross']*100:+.2f}%" if f["avg_excess_gross"] is not None else "–"
        exn = f"{f['avg_excess_net']*100:+.2f}%" if f["avg_excess_net"] is not None else "–"
        top2 = (f"{f['top2_share_of_gain']*100:.0f}%"
                if f.get("top2_share_of_gain") is not None else "–")
        print(f'{r["name"]:<32}{ph:>6}{f["n"]:>7}{hr["half1"]["n"]:>7}{hr["half2"]["n"]:>7}'
              f'{beat:>10}{exg:>11}{exn:>11}{top2:>9}  {r["verdict"]}')
    print(f"\n寫入 {OUT}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()

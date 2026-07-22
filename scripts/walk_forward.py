# -*- coding: utf-8 -*-
"""滾動出樣本驗證：判斷老師是真技術還是運氣。

兩個獨立檢驗，都只用「過去」資訊做決策，避免回頭看的自我實現偏誤：

1) 前後半持續性：把每位老師「已到期」的推薦按時間切兩半，比較前半 vs 後半的
   平均超額。有技術 → 前半好的後半也好（正相關）；純運氣 → 無相關。

2) 滾動跟單模擬：沿時間軸逐集前進，每一集只用「到目前為止」的戰績判定誰是
   「當下前段班」(過去樣本≥MIN 且累積超額為正)，再記錄該集這些老師推薦的
   實際後續超額。這是真正的 out-of-sample：跟單當下只知道過去。

用法：python walk_forward.py
"""
import sys
from collections import defaultdict

from build_dashboard import load_data

MIN_HISTORY = 5   # 要先累積幾筆到期推薦，才把一位老師納入評比


def matured_picks(episodes, horizon="m1"):
    """指定持有期已到期的計分推薦，按播出日排序（舊→新）。

    d3(3交易日)幾乎每集都到期→樣本最多、檢定力最強；m1 樣本較少但更貼近波段。
    """
    out = []
    for ep in episodes:
        for t in ep["teachers"]:
            for p in t["picks"]:
                if p["stance"] not in ("看多", "看空") or not p.get("perf"):
                    continue
                ex = (p.get("excess") or {}).get(horizon)
                if ex is None:
                    continue
                sign = -1 if p["stance"] == "看空" else 1
                out.append({"date": ep["date"], "teacher": t["name"],
                            "sex": sign * ex})
    out.sort(key=lambda r: r["date"])
    return out


def persistence(picks):
    print("=== 檢驗1：老師前後半持續性（技術應該前後一致）===")
    by_t = defaultdict(list)
    for r in picks:
        by_t[r["teacher"]].append(r["sex"])
    rows = []
    for name, seq in by_t.items():
        if len(seq) < 8:
            continue
        half = len(seq) // 2
        a = sum(seq[:half]) / half
        b = sum(seq[half:]) / (len(seq) - half)
        rows.append((name, len(seq), a, b))
    rows.sort(key=lambda x: -x[2])
    print(f'  {"老師":<8}{"樣本":>4}{"前半超額":>9}{"後半超額":>9}   前半好→後半是否續好')
    same = 0
    for name, n, a, b in rows:
        tag = "✓ 一致" if (a > 0) == (b > 0) else "✗ 反轉"
        if (a > 0) == (b > 0):
            same += 1
        print(f'  {name:<9}{n:>4}{a*100:>+8.1f}%{b*100:>+8.1f}%   {tag}')
    if rows:
        # 前半正超額的老師，後半是否仍正
        pos_a = [r for r in rows if r[2] > 0]
        kept = [r for r in pos_a if r[3] > 0]
        print(f'  → 前半「正超額」老師 {len(pos_a)} 位，後半仍正的 {len(kept)} 位'
              f'（{len(kept)/len(pos_a)*100:.0f}%）；方向一致 {same}/{len(rows)}')
        print('  （若接近一半 = 跟丟硬幣沒兩樣 = 看不出技術；明顯過半才是持續性訊號）')


def walk_forward(picks):
    print()
    print("=== 檢驗2：滾動跟單（每集只用過去戰績決定跟誰）===")
    hist = defaultdict(lambda: [0, 0.0])   # teacher -> [n, sum_sex]
    followed, allp = [], []
    for r in picks:
        allp.append(r["sex"])
        n, s = hist[r["teacher"]]
        # 用「這筆之前」的戰績判定：過去樣本夠且累積超額為正 = 當下前段班
        if n >= MIN_HISTORY and s > 0:
            followed.append(r["sex"])
        hist[r["teacher"]][0] += 1
        hist[r["teacher"]][1] += r["sex"]

    def rep(tag, xs):
        if not xs:
            print(f'  {tag}: n=0')
            return
        n = len(xs)
        print(f'  {tag:<28} n={n:>3}  贏大盤={sum(1 for e in xs if e>0)/n*100:>3.0f}%'
              f'  平均超額={sum(xs)/n*100:>+5.1f}%')
    rep("照單全收(基準)", allp)
    rep("只跟『當下前段班』老師", followed)
    print('  （若「當下前段班」明顯優於基準 = 過去戰績真的能預測未來 = 有技術可挖；'
          '若差不多或更差 = 過去不能預測未來 = 目前只是運氣）')


def main():
    eps = load_data()
    for h, label in [("d3", "＋3日（樣本最多、檢定力最強）"), ("m1", "＋1月")]:
        picks = matured_picks(eps, h)
        if not picks:
            continue
        print("#" * 60)
        print(f'# 持有期 {label}：已到期推薦 {len(picks)} 筆'
              f'（{picks[0]["date"]} ~ {picks[-1]["date"]}）')
        print("#" * 60)
        persistence(picks)
        walk_forward(picks)
        print()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()

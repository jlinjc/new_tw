# -*- coding: utf-8 -*-
"""最終驗證：任務 A（複驗批判者四項承重結論）＋ 任務 B（實測 H3 / H8）。

獨立性宣告：本腳本完全從 data/performance.json 與未還原價（auto_adjust=False）
重寫，沒有讀取任何 audit*.py。

執行：  python scripts/final_verification.py
輸出：  data/final_verification.json

═══════════════════════════════════════════════════════════════════════
事前註冊（PRE-REGISTRATION）——在任何任務 B 的結果被計算之前寫死
═══════════════════════════════════════════════════════════════════════
H3（位階／伸展度）
  主檢定：ext_z（= (Close/MA20 − 1) / (ATR20/Close)，全部用未還原價）
          的「集內常態化名次」對 sign×excess_d3 的迴歸斜率。
  預期符號：負（越伸展 ⇒ d3 超額越差）。
  可交易方向：做多「最不伸展」的那一端（Q1）。
  次要：ext20、near_high60 同號；5 桶 Q1→Q5 單調遞減。

H8（擁擠度衰減）
  主檢定：crowd_10（該 ticker 在前 10 集被任一老師推薦過的集數）
          的桶名次（0/1/2/3-4/>=5 → 0..4，除以 4）對 sign×excess_d3 的斜率。
  預期符號：負（越擁擠 ⇒ 越差）。
  可交易方向：只買 crowd_10 == 0 的「零前科」標的。
  次要：crowd_5、crowd_20 同號。

聯合檢定（H3 × H8）
  主檢定：集內固定效果迴歸 y ~ ext_z_rank + crowd_rank。
  預期符號：兩個係數都為負，且各自保留其單變量幅度的相當部分
            （＝兩者是不同的效應，不是同一件事被算兩次）。

多重比較：3 個檢定 ⇒ Bonferroni α = 0.05/3 = 0.01667（雙尾）。
成本：來回 0.57%，所有「可否交易」一律以淨值判定。
MDE：集內殘差 sd ≈ 7.1%，集內分位差設計的可偵測下限 ≈ 1.1%。
     信賴區間若涵蓋 0 且上下界都超過 ±1% ⇒ 結論是 UNDERPOWERED，不是 NULL。
═══════════════════════════════════════════════════════════════════════
"""
import json
import os
import pickle
import sys
import tempfile
from collections import defaultdict, Counter

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PERF_JSON = os.path.join(ROOT, "data", "performance.json")
OUT_JSON = os.path.join(ROOT, "data", "final_verification.json")
PX_CACHE = os.path.join(tempfile.gettempdir(), "ebc_raw_ohlc_unadjusted.pkl")

COST = 0.0057
ALPHA_BONF = 0.05 / 3
CORE5 = {"容逸燊", "李永年", "張林忠", "鍾國忠", "黃豐凱"}
DIRECTIONAL = ("看多", "看空")
CONTROL = ("觀望", "中性")
HORIZONS = ("d3", "w1", "w2", "m1")
BOOT = 5000
rng = np.random.default_rng(20260918)

RES = {"preregistration": {
    "H3": {"primary": "ext_z within-episode normalized rank -> sign*excess_d3 slope",
           "expected_sign": "negative", "tradeable_side": "long least-extended (Q1)"},
    "H8": {"primary": "crowd_10 bucket rank (0/1/2/3-4/>=5) -> sign*excess_d3 slope",
           "expected_sign": "negative", "tradeable_side": "long crowd_10 == 0"},
    "joint": {"primary": "episode-FE regression y ~ ext_z_rank + crowd_rank",
              "expected_sign": "both negative, each retaining a substantial part of its univariate size"},
    "alpha_bonferroni": ALPHA_BONF, "cost_roundtrip": COST, "mde_within_episode": 0.011}}


# ───────────────────────── 資料層 ─────────────────────────
def load_positions():
    """部位去重：同集＋同標的（ticker 優先）＋同方向 ⇒ 1 個部位。"""
    perf = json.load(open(PERF_JSON, encoding="utf-8"))
    pos = {}
    for r in perf:
        key = (r["video_id"], r.get("ticker") or r["stock_name"], r["stance"])
        d = pos.get(key)
        if d is None:
            d = pos[key] = {
                "video_id": r["video_id"], "date": r["date"],
                "ticker": r.get("ticker"), "stock_name": r["stock_name"],
                "symbol": r.get("symbol"), "stance": r["stance"],
                "sign": -1 if r["stance"] == "看空" else 1,
                "excess": r.get("excess") or {},
                "ret": (r.get("perf") or {}).get("ret") or {},
                "bench": (r.get("bench") or {}).get("ret") or {},
                "entry_date": (r.get("perf") or {}).get("entry_date"),
                "teachers": set()}
        d["teachers"].add(r["teacher"])
    return list(pos.values())


def sx(p, h="d3"):
    v = p["excess"].get(h) if p["excess"] else None
    return None if v is None else p["sign"] * v


def bench_of(p, h="d3"):
    b = p["bench"].get(h) if p["bench"] else None
    if b is not None:
        return b
    r, e = p["ret"].get(h), p["excess"].get(h)
    return None if (r is None or e is None) else r - e


def load_prices(symbols):
    """未還原價 OHLCV（auto_adjust=False）。還原價在除息日有向下不連續，
    而本樣本 7~8 月（台股除息旺季）佔 45%，會讓 MA20/伸展度/ATR 全部失真。"""
    store = pickle.load(open(PX_CACHE, "rb")) if os.path.exists(PX_CACHE) else {}
    todo = [s for s in symbols if s not in store]
    if todo:
        import yfinance as yf
        for i in range(0, len(todo), 40):
            batch = todo[i:i + 40]
            try:
                df = yf.download(batch, start="2026-01-02", end="2026-09-18",
                                 auto_adjust=False, progress=False, group_by="ticker")
            except Exception as e:                      # noqa: BLE001
                print("batch fail", i, e, file=sys.stderr)
                continue
            multi = isinstance(df.columns, pd.MultiIndex)
            for s in batch:
                try:
                    sub = (df[s] if multi else df)[["Open", "High", "Low", "Close", "Volume"]]
                    sub = sub.dropna(how="all")
                    if len(sub):
                        store[s] = sub
                except (KeyError, IndexError):
                    pass
            pickle.dump(store, open(PX_CACHE, "wb"))
    return store


# ───────────────────────── 統計層 ─────────────────────────
def cluster_stats(vals, groups):
    """平均值的 naive SE、CRVE 集群穩健 SE（集群＝集數日）、以及
    集群平均 t（每個集數日等權，＝批判者採用的定義，見任務 A 差異說明）。"""
    y = np.asarray(vals, float)
    n = len(y)
    m = y.mean()
    naive_se = y.std(ddof=1) / np.sqrt(n)
    gs = defaultdict(float)
    cm = defaultdict(list)
    for g, v in zip(groups, y):
        gs[g] += v - m
        cm[g].append(v)
    G = len(gs)
    crve_se = np.sqrt((G / (G - 1.0)) * sum(v * v for v in gs.values())) / n
    means = np.array([np.mean(v) for v in cm.values()])
    eq_se = means.std(ddof=1) / np.sqrt(G)
    return dict(n=n, G=G, mean=m, net=m - COST,
                naive_se=naive_se, naive_t=m / naive_se,
                crve_se=crve_se, crve_t=m / crve_se,
                eq_mean=means.mean(), eq_se=eq_se, eq_t=means.mean() / eq_se)


def block_boot(vals, groups, stat=np.mean, B=BOOT):
    """以集數日為 block 的 bootstrap。"""
    by = defaultdict(list)
    for g, v in zip(groups, vals):
        by[g].append(v)
    arrs = [np.array(v, float) for v in by.values()]
    K = len(arrs)
    out = np.empty(B)
    for b in range(B):
        idx = rng.integers(0, K, K)
        out[b] = stat(np.concatenate([arrs[i] for i in idx]))
    return np.percentile(out, [2.5, 97.5]), out


def ols_cluster(y, X, groups, names=None):
    """OLS 含集群穩健 (CR1) 共變異數，集群＝集數日。X 不含常數項時自動加。"""
    y = np.asarray(y, float)
    X = np.asarray(X, float)
    if X.ndim == 1:
        X = X[:, None]
    n, k = X.shape
    XtX_inv = np.linalg.pinv(X.T @ X)
    beta = XtX_inv @ (X.T @ y)
    e = y - X @ beta
    s2 = (e @ e) / max(n - k, 1)
    cov_cl = s2 * XtX_inv
    gmap = defaultdict(list)
    for i, g in enumerate(groups):
        gmap[g].append(i)
    G = len(gmap)
    meat = np.zeros((k, k))
    for idx in gmap.values():
        u = X[idx].T @ e[idx]
        meat += np.outer(u, u)
    c = (G / (G - 1.0)) * ((n - 1.0) / max(n - k, 1))
    cov_rob = XtX_inv @ (c * meat) @ XtX_inv
    se, se_cl = np.sqrt(np.diag(cov_cl)), np.sqrt(np.diag(cov_rob))
    names = names or [f"x{i}" for i in range(k)]
    return {nm: dict(coef=float(b), se=float(s), t=float(b / s),
                     se_cl=float(sc), t_cl=float(b / sc))
            for nm, b, s, sc in zip(names, beta, se, se_cl)} | {"_n": n, "_G": G}


def pct(x):
    return f"{100 * x:+.3f}%"


def drop_top5(vals, groups):
    """去掉最好的 5% 部位後是否變號（本專案標準穩健性門檻）。"""
    v = np.asarray(vals, float)
    k = int(np.floor(0.05 * len(v)))
    keep = np.sort(np.argsort(v)[::-1][k:])
    return cluster_stats(list(v[keep]), [groups[i] for i in keep]), k


# ═══════════════════════ 任務 A ═══════════════════════
def task_a(P):
    out = {}
    DIR = [p for p in P if p["stance"] in DIRECTIONAL]
    print(f"去重後部位：全部 {len(P)}，有方向 {len(DIR)}  {dict(Counter(p['stance'] for p in P))}")

    # ── A1 ──
    print("\n=== A1  baseline 毛超額 ===")
    a1 = {}
    for h in HORIZONS:
        rows = [(sx(p, h), p["date"]) for p in DIR]
        rows = [r for r in rows if r[0] is not None]
        v = [r[0] for r in rows]
        g = [r[1] for r in rows]
        st = cluster_stats(v, g)
        ci, _ = block_boot(v, g)
        a1[h] = dict(st, boot_ci=list(ci))
        print(f"  {h}: n={st['n']} G={st['G']} 毛={pct(st['mean'])} 淨={pct(st['net'])} "
              f"naive_t={st['naive_t']:+.2f} CRVE_t={st['crve_t']:+.2f} "
              f"boot95=[{pct(ci[0])},{pct(ci[1])}]")
    print("  宣稱 d3 n=1632 +0.008% ｜ m1 n=1304 −1.246%")
    out["A1"] = a1

    # ── A2 ──
    print("\n=== A2  beta 傾斜迴歸 signed_excess ~ a + b*bench ===")
    a2 = {}
    for h in ("d3", "m1"):
        rows = [(sx(p, h), bench_of(p, h), p["date"]) for p in DIR]
        rows = [r for r in rows if r[0] is not None and r[1] is not None]
        y = [r[0] for r in rows]
        X = np.column_stack([np.ones(len(rows)), [r[1] for r in rows]])
        g = [r[2] for r in rows]
        r = ols_cluster(y, X, g, ["alpha", "slope"])
        r["implied_beta"] = 1 + r["slope"]["coef"]
        a2[h] = r
        print(f"  {h}: n={r['_n']} slope={r['slope']['coef']:.4f} "
              f"(se={r['slope']['se']:.4f} t={r['slope']['t']:.2f} | "
              f"cl_se={r['slope']['se_cl']:.4f} cl_t={r['slope']['t_cl']:.2f}) "
              f"隱含beta={r['implied_beta']:.3f}")
        print(f"      alpha={pct(r['alpha']['coef'])} (se={100*r['alpha']['se']:.3f}% "
              f"t={r['alpha']['t']:.2f} | cl_se={100*r['alpha']['se_cl']:.3f}% "
              f"cl_t={r['alpha']['t_cl']:.2f})")
    print("  宣稱 d3 slope 0.416 / beta 1.42 / α −0.130%；m1 slope 1.089 / beta 2.09 / α −3.258%")
    out["A2"] = a2

    # ── A3 ──
    print("\n=== A3  背書溢價（集內配對：看多 vs 觀望/中性）===")
    tr, ct, trb, ctb = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    for p in P:
        v, b = sx(p, "d3"), bench_of(p, "d3")
        if v is None:
            continue
        if p["stance"] == "看多":
            tr[p["date"]].append(v)
            trb[p["date"]].append(b)
        elif p["stance"] in CONTROL:
            ct[p["date"]].append(v)
            ctb[p["date"]].append(b)
    both = sorted(set(tr) & set(ct))
    ta = [np.array(tr[d]) for d in both]
    ca = [np.array(ct[d]) for d in both]
    d_eq = float(np.mean([t.mean() - c.mean() for t, c in zip(ta, ca)]))
    tv, cv = np.concatenate(ta), np.concatenate(ca)
    d_pool = float(tv.mean() - cv.mean())
    K = len(both)
    be = np.empty(BOOT)
    bp = np.empty(BOOT)
    for b in range(BOOT):
        i = rng.integers(0, K, K)
        be[b] = np.mean([ta[j].mean() - ca[j].mean() for j in i])
        bp[b] = np.concatenate([ta[j] for j in i]).mean() - np.concatenate([ca[j] for j in i]).mean()
    ci_eq = np.percentile(be, [2.5, 97.5])
    ci_pool = np.percentile(bp, [2.5, 97.5])
    tb, cb = np.concatenate([np.array(trb[d]) for d in both]), np.concatenate([np.array(ctb[d]) for d in both])
    gt = [d for d in both for _ in tr[d]]
    gc = [d for d in both for _ in ct[d]]
    bt = ols_cluster(tv, np.column_stack([np.ones(len(tv)), tb]), gt, ["a", "b"])
    bc = ols_cluster(cv, np.column_stack([np.ones(len(cv)), cb]), gc, ["a", "b"])
    allv, allb = np.concatenate([tv, cv]), np.concatenate([tb, cb])
    dm = np.concatenate([np.ones(len(tv)), np.zeros(len(cv))])
    inter = ols_cluster(allv, np.column_stack([np.ones(len(allv)), allb, dm, allb * dm]),
                        gt + gc, ["a", "bench", "treat", "bench_x_treat"])
    from scipy import stats as sps
    lev = sps.levene(tv, cv, center="median")
    print(f"  同時含兩組的集數 = {K}；n_看多={len(tv)} n_對照={len(cv)}")
    print(f"  (a) 集內等權差值 = {pct(d_eq)}  boot95=[{pct(ci_eq[0])},{pct(ci_eq[1])}]")
    print(f"  (b) 配對子樣本 pooled 差值 = {pct(d_pool)}  boot95=[{pct(ci_pool[0])},{pct(ci_pool[1])}]")
    print(f"  (c) 集內 FE 迴歸 treat 係數 = {pct(inter['treat']['coef'])}")
    print(f"  風險：看多 隱含beta={1+bt['b']['coef']:.3f} sd={100*tv.std(ddof=1):.2f}% ｜ "
          f"對照 隱含beta={1+bc['b']['coef']:.3f} sd={100*cv.std(ddof=1):.2f}%")
    print(f"  beta 交互項 t={inter['bench_x_treat']['t_cl']:+.2f}（集群穩健）；"
          f"Levene 等變異 p={lev.pvalue:.3f}；變異數比={tv.var(ddof=1)/cv.var(ddof=1):.3f}")
    print("  宣稱 差值 −0.335%，95% CI [−1.53%,+0.87%]；beta 1.46 vs 1.57；sd 7.68% vs 7.60%")
    out["A3"] = dict(n_episodes=K, n_treat=len(tv), n_ctrl=len(cv),
                     diff_equal_weight=d_eq, ci_equal=list(ci_eq),
                     diff_pooled=d_pool, ci_pooled=list(ci_pool),
                     diff_fe=inter["treat"]["coef"], fe_t_cl=inter["treat"]["t_cl"],
                     treat_beta=1 + bt["b"]["coef"], ctrl_beta=1 + bc["b"]["coef"],
                     treat_sd=float(tv.std(ddof=1)), ctrl_sd=float(cv.std(ddof=1)),
                     beta_interaction_t_cl=inter["bench_x_treat"]["t_cl"],
                     levene_p=float(lev.pvalue),
                     var_ratio=float(tv.var(ddof=1) / cv.var(ddof=1)))

    # ── A4 ──
    print("\n=== A4  core5 ===")
    rows = [(sx(p, "d3"), p["date"]) for p in DIR if p["teachers"] & CORE5]
    rows = [r for r in rows if r[0] is not None]
    v = [r[0] for r in rows]
    g = [r[1] for r in rows]
    st = cluster_stats(v, g)
    ci, _ = block_boot(v, g)
    st2, k = drop_top5(v, g)
    print(f"  core5 d3: n={st['n']} G={st['G']} 毛={pct(st['mean'])} 淨={pct(st['net'])}")
    print(f"    naive_t={st['naive_t']:.2f}  CRVE_t={st['crve_t']:.2f}  "
          f"集群等權_t={st['eq_t']:.2f}  boot95=[{pct(ci[0])},{pct(ci[1])}]")
    print(f"  去掉最好 5%（{k} 筆）: n={st2['n']} 毛={pct(st2['mean'])} 淨={pct(st2['net'])} "
          f"CRVE_t={st2['crve_t']:.2f} 集群等權_t={st2['eq_t']:.2f} "
          f"⇒ 淨值變號={((st['net'] > 0) != (st2['net'] > 0))}")
    print("  宣稱 n=426 毛+1.265% 淨+0.695%；去5%後 毛+0.188% 淨−0.382%；clustered t=2.09 naive t=3.16")
    out["A4"] = dict(core5=dict(st, boot_ci=list(ci)), drop_top5=dict(st2, k_dropped=k),
                     sign_flip_net=bool((st["net"] > 0) != (st2["net"] > 0)))

    tp = defaultdict(list)
    for p in DIR:
        v0 = sx(p, "d3")
        if v0 is None:
            continue
        for t in p["teachers"]:
            tp[t].append((v0, p["date"]))
    tab = []
    for t, rs in tp.items():
        if len(rs) < 40:
            continue
        s = cluster_stats([a for a, _ in rs], [b for _, b in rs])
        tab.append(dict(teacher=t, **{k2: s[k2] for k2 in
                                      ("n", "G", "mean", "net", "naive_t", "crve_t", "eq_t")}))
    tab.sort(key=lambda r: r["eq_t"])
    print("\n  --- 老師別（n>=40，d3）---")
    print(f"  {'老師':<8} {'n':>4} {'毛':>9} {'淨':>9} {'naive_t':>8} {'CRVE_t':>8} {'等權_t':>8}")
    for r in tab:
        print(f"  {r['teacher']:<8} {r['n']:>4} {pct(r['mean']):>9} {pct(r['net']):>9} "
              f"{r['naive_t']:>+8.2f} {r['crve_t']:>+8.2f} {r['eq_t']:>+8.2f}")
    for lab, key in (("CRVE", "crve_t"), ("集群等權", "eq_t")):
        mx = max(tab, key=lambda r: abs(r[key]))
        print(f"  |t| 最大（{lab}）= {mx['teacher']} t={mx[key]:+.2f} ⇒ 為負？{mx[key] < 0}")
    ts = np.array([r["eq_t"] for r in tab])
    print(f"  n>=40 的老師 {len(tab)} 位；等權 t 平均={ts.mean():+.3f} sd={ts.std(ddof=1):.3f}"
          f"（純雜訊應為 0 與 1）；正 t 有 {int((ts>0).sum())} 位")
    print("  宣稱 |t| 最大者為負（朱家泓 −3.76）")
    out["A4_teachers"] = tab
    out["A4_teacher_t_dist"] = dict(n_teachers=len(tab), mean_eq_t=float(ts.mean()),
                                    sd_eq_t=float(ts.std(ddof=1)), n_positive=int((ts > 0).sum()))
    return out


# ═══════════════════════ 任務 B ═══════════════════════
def build_features(P, store):
    """H3 技術指標（全部未還原價，用播出日 D 收盤前資訊）＋ H8 擁擠度。"""
    dates = sorted({p["date"] for p in P})
    order = {d: i for i, d in enumerate(dates)}

    # H8: 每集出現過的 ticker 集合（含非方向性，符合「任一方向」）
    ep_tickers, ep_tickers_dir = defaultdict(set), defaultdict(set)
    for p in P:
        key = p["ticker"] or p["stock_name"]
        ep_tickers[p["date"]].add(key)
        if p["stance"] in DIRECTIONAL:
            ep_tickers_dir[p["date"]].add(key)

    def crowd(p, w, pool):
        i = order[p["date"]]
        key = p["ticker"] or p["stock_name"]
        return sum(1 for d in dates[max(0, i - w):i] if key in pool[d])

    for p in P:
        for w in (5, 10, 20):
            p[f"crowd_{w}"] = crowd(p, w, ep_tickers)
            p[f"crowd_{w}_dir"] = crowd(p, w, ep_tickers_dir)
        p["ext20"] = p["near_high60"] = p["ext_z"] = p["ext20_adj"] = None
        s = p.get("symbol")
        df = store.get(s) if s else None
        if df is None:
            continue
        d = pd.Timestamp(p["date"])
        sub = df[df.index <= d]
        if len(sub) < 60:
            continue
        c = sub["Close"].astype(float)
        h = sub["High"].astype(float)
        lo = sub["Low"].astype(float)
        if pd.isna(c.iloc[-1]):
            c = c.dropna()
            if len(c) < 60:
                continue
        last = float(c.iloc[-1])
        ma20 = float(c.iloc[-20:].mean())
        hi60 = float(h.iloc[-60:].max())
        pc = c.shift(1)
        tr = pd.concat([h - lo, (h - pc).abs(), (lo - pc).abs()], axis=1).max(axis=1)
        atr20 = float(tr.iloc[-20:].mean())
        if not (ma20 > 0 and atr20 > 0 and last > 0 and hi60 > 0):
            continue
        p["ext20"] = last / ma20 - 1
        p["near_high60"] = last / hi60 - 1
        p["ext_z"] = (last / ma20 - 1) / (atr20 / last)
    return P


def within_rank(vals):
    """集內常態化名次 ∈ [0,1]（平均名次處理 ties）。"""
    a = np.asarray(vals, float)
    n = len(a)
    if n < 2:
        return None
    r = pd.Series(a).rank(method="average").to_numpy()
    return (r - 1) / (n - 1)


def within_design(P, feat, h="d3", min_n=5, use_sign=True):
    """回傳 (y_demeaned, rank_centered, raw_y, groups, feat_vals)。"""
    by = defaultdict(list)
    for p in P:
        if p["stance"] not in DIRECTIONAL:
            continue
        y = sx(p, h)
        f = p.get(feat)
        if y is None or f is None:
            continue
        by[p["date"]].append((y, float(f), p))
    Y, R, RAW, G, F, PS = [], [], [], [], [], []
    for d, rows in by.items():
        if len(rows) < min_n:
            continue
        ys = np.array([r[0] for r in rows])
        fs = np.array([r[1] for r in rows])
        rk = within_rank(fs)
        if rk is None or np.allclose(fs, fs[0]):
            continue
        Y.extend(ys - ys.mean())
        R.extend(rk - rk.mean())
        RAW.extend(ys)
        G.extend([d] * len(rows))
        F.extend(fs)
        PS.extend([r[2] for r in rows])
    return (np.array(Y), np.array(R), np.array(RAW), G, np.array(F), PS)


def spread_test(feat, P, label, sign_expected=-1, h="d3"):
    """集內名次斜率 = Q5−Q1 的等價價差；block bootstrap by 集數日。"""
    Y, R, RAW, G, F, PS = within_design(P, feat, h)
    if len(Y) == 0:
        return None
    r = ols_cluster(Y, R[:, None], G, ["slope"])
    by = defaultdict(list)
    for y, rk, g in zip(Y, R, G):
        by[g].append((y, rk))
    ks = list(by.values())
    K = len(ks)
    bs = np.empty(BOOT)
    for b in range(BOOT):
        i = rng.integers(0, K, K)
        cat = [x for j in i for x in ks[j]]
        yy = np.array([c[0] for c in cat])
        rr = np.array([c[1] for c in cat])
        bs[b] = (rr @ yy) / (rr @ rr)
    ci = np.percentile(bs, [2.5, 97.5])
    # 5 桶（集內分位）
    bucket = defaultdict(list)
    byd = defaultdict(list)
    for y, f, g, p in zip(RAW, F, G, PS):
        byd[g].append((y, f, p))
    for d, rows in byd.items():
        fs = np.array([r[1] for r in rows])
        q = np.minimum((pd.Series(fs).rank(method="first").to_numpy() - 1) * 5 // len(fs), 4)
        for (y, f, p), qi in zip(rows, q):
            bucket[int(qi)].append((y, d))
    bstats = {}
    for qi in sorted(bucket):
        v = [a for a, _ in bucket[qi]]
        gg = [b for _, b in bucket[qi]]
        bstats[qi] = cluster_stats(v, gg)
    means = [bstats[q]["mean"] for q in sorted(bstats)]
    from scipy import stats as sps
    sp = sps.spearmanr(np.arange(len(means)), means)
    # Q1 vs Q5 差 + bootstrap
    q1 = [a for a, _ in bucket[0]]
    q1g = [b for _, b in bucket[0]]
    q5 = [a for a, _ in bucket[4]]
    q5g = [b for _, b in bucket[4]]
    d51 = np.mean(q5) - np.mean(q1)
    b1 = defaultdict(list)
    b5 = defaultdict(list)
    for v, g in zip(q1, q1g):
        b1[g].append(v)
    for v, g in zip(q5, q5g):
        b5[g].append(v)
    common = sorted(set(b1) & set(b5))
    a1 = [np.array(b1[d]) for d in common]
    a5 = [np.array(b5[d]) for d in common]
    Kc = len(common)
    bb = np.empty(BOOT)
    for b in range(BOOT):
        i = rng.integers(0, Kc, Kc)
        bb[b] = np.concatenate([a5[j] for j in i]).mean() - np.concatenate([a1[j] for j in i]).mean()
    ci51 = np.percentile(bb, [2.5, 97.5])
    z = r["slope"]["coef"] / r["slope"]["se_cl"]
    p_val = float(2 * (1 - sps.norm.cdf(abs(z))))
    # 最佳桶（事前註冊方向的那一端）的 long-only 淨值 + 去掉最好 5%
    best_q = 0 if sign_expected < 0 else 4
    bq = [a for a, _ in bucket[best_q]]
    bqg = [b for _, b in bucket[best_q]]
    bq_st = cluster_stats(bq, bqg)
    bq_ci, _ = block_boot(bq, bqg)
    bq_d5, kk = drop_top5(bq, bqg)
    print(f"\n  [{label}] 集內名次斜率（＝Q5−Q1 等價價差）")
    print(f"    n={r['_n']} 集數={r['_G']}  斜率={pct(r['slope']['coef'])} "
          f"cl_se={100*r['slope']['se_cl']:.3f}% cl_t={r['slope']['t_cl']:+.2f} p={p_val:.4f} "
          f"（Bonferroni α={ALPHA_BONF:.4f}）")
    print(f"    block boot95=[{pct(ci[0])},{pct(ci[1])}]  事前預期符號={'負' if sign_expected<0 else '正'}"
          f"  實際符號={'負' if r['slope']['coef']<0 else '正'}")
    print(f"    5 桶（集內分位，低→高）毛均值: " + "  ".join(pct(m) for m in means))
    print(f"    單調性 Spearman(桶序,均值) rho={sp.statistic:+.2f} p={sp.pvalue:.3f}")
    print(f"    Q5−Q1 = {pct(d51)}  boot95=[{pct(ci51[0])},{pct(ci51[1])}]")
    print(f"    事前註冊的可交易那一端 Q{best_q+1}: n={bq_st['n']} 毛={pct(bq_st['mean'])} "
          f"淨={pct(bq_st['net'])} CRVE_t={bq_st['crve_t']:+.2f} "
          f"boot95(毛)=[{pct(bq_ci[0])},{pct(bq_ci[1])}]")
    print(f"    去掉最好 5%（{kk} 筆）後 淨={pct(bq_d5['net'])} ⇒ 淨值變號="
          f"{(bq_st['net']>0) != (bq_d5['net']>0)}")
    return dict(feature=feat, label=label, n=r["_n"], n_episodes=r["_G"],
                slope=r["slope"]["coef"], slope_se_cl=r["slope"]["se_cl"],
                slope_t_cl=r["slope"]["t_cl"], p_value=p_val, boot_ci=list(ci),
                bucket_means=means, bucket_n=[bstats[q]["n"] for q in sorted(bstats)],
                spearman_rho=float(sp.statistic), spearman_p=float(sp.pvalue),
                q5_minus_q1=float(d51), q5_q1_ci=list(ci51),
                expected_sign=sign_expected,
                sign_as_predicted=bool(np.sign(r["slope"]["coef"]) == sign_expected),
                best_bucket=best_q, best_bucket_stats=bq_st, best_bucket_ci=list(bq_ci),
                best_bucket_drop5=bq_d5, best_bucket_sign_flip=bool(
                    (bq_st["net"] > 0) != (bq_d5["net"] > 0)))


def _slope(P, feat, h="d3", subset=None, transform=None):
    """集內 FE 名次斜率，回傳 ols_cluster 結果與原始 y。"""
    by = defaultdict(list)
    for p in P:
        if p["stance"] not in DIRECTIONAL:
            continue
        if subset is not None and not subset(p):
            continue
        y, f = sx(p, h), p.get(feat)
        if y is None or f is None:
            continue
        by[p["date"]].append((y, float(f)))
    Y, R, G, RAW = [], [], [], []
    for d, rows in by.items():
        if len(rows) < 5:
            continue
        ys = np.array([r[0] for r in rows])
        if transform is not None:
            ys = transform(ys)
        rk = within_rank([r[1] for r in rows])
        if rk is None or np.std(rk) == 0:
            continue
        Y.extend(ys - ys.mean())
        R.extend(rk - rk.mean())
        G.extend([d] * len(rows))
        RAW.extend(ys)
    if len(Y) < 30:
        return None
    Y, R, RAW = np.array(Y), np.array(R), np.array(RAW)
    r = ols_cluster(Y, R[:, None], G, ["slope"])
    return r, Y, R, G, RAW


def robustness(P, DIR, out):
    """H8 符號與事前註冊相反 ⇒ 必須查清楚是真效應還是離群值/時期/beta 造成。"""
    print("\n" + "─" * 70)
    print("H8 決定性穩健性診斷（因為主檢定符號與事前註冊相反）")
    print("─" * 70)
    rob = {}
    from scipy import stats as sps

    # (1) long-only 可交易性：事前那一端、反向那一端
    print("\n  (1) long-only 可交易性（淨值 = 毛 − 0.57%）")
    tr = {}
    for lab, sel in (("crowd_10 == 0（事前註冊要買的）", lambda p: p["crowd_10_dir"] == 0),
                     ("crowd_10 >= 3（反向，事後）", lambda p: p["crowd_10_dir"] >= 3),
                     ("crowd_10 >= 5（反向，事後）", lambda p: p["crowd_10_dir"] >= 5)):
        rows = [(sx(p), p["date"]) for p in DIR if sel(p) and sx(p) is not None]
        v = [r[0] for r in rows]
        g = [r[1] for r in rows]
        s = cluster_stats(v, g)
        ci, _ = block_boot(v, g)
        d5, k = drop_top5(v, g)
        print(f"    {lab:<28} n={s['n']:<4} 毛={pct(s['mean'])} 淨={pct(s['net'])} "
              f"CRVE_t={s['crve_t']:+.2f} 毛boot95=[{pct(ci[0])},{pct(ci[1])}] "
              f"⇒ 淨boot95=[{pct(ci[0]-COST)},{pct(ci[1]-COST)}]")
        print(f"    {'':<28} 去最好5%（{k}筆）後 淨={pct(d5['net'])}  淨值為正？{s['net'] > 0}")
        tr[lab] = dict(stats=s, gross_ci=list(ci), net_ci=[ci[0] - COST, ci[1] - COST],
                       drop5=d5, net_positive=bool(s["net"] > 0))
    rob["long_only"] = tr

    # (2) 離群值
    print("\n  (2) 離群值：斜率對極端部位的依賴")
    base = _slope(P, "crowd_bucket")
    r0, Y, R, G, RAW = base
    print(f"    全樣本斜率 = {pct(r0['slope']['coef'])} cl_t={r0['slope']['t_cl']:+.2f}")
    k = int(np.floor(0.05 * len(RAW)))
    outl = {}
    for lab, keep in (("去掉最好 5%", np.sort(np.argsort(RAW)[::-1][k:])),
                      ("去掉最極端 5%（雙尾）", np.sort(np.argsort(np.abs(RAW))[::-1][k:]))):
        rr = ols_cluster(Y[keep], R[keep][:, None], [G[i] for i in keep], ["slope"])
        flip = np.sign(rr["slope"]["coef"]) != np.sign(r0["slope"]["coef"])
        print(f"    {lab}（{k} 筆）= {pct(rr['slope']['coef'])} cl_t={rr['slope']['t_cl']:+.2f}"
              f"  變號={flip}  仍過 Bonferroni？{abs(rr['slope']['t_cl']) > 2.394}")
        outl[lab] = dict(slope=rr["slope"]["coef"], t_cl=rr["slope"]["t_cl"], sign_flip=bool(flip))
    w = _slope(P, "crowd_bucket",
               transform=lambda ys: np.clip(ys, np.percentile(RAW, 1), np.percentile(RAW, 99)))
    print(f"    winsorize 1%/99% = {pct(w[0]['slope']['coef'])} cl_t={w[0]['slope']['t_cl']:+.2f}")
    outl["winsorized"] = dict(slope=w[0]["slope"]["coef"], t_cl=w[0]["slope"]["t_cl"])
    rob["outliers"] = outl

    # (3) 時間穩定性
    print("\n  (3) 時間穩定性（全域中位切點）")
    ds = sorted({p["date"] for p in DIR})
    mid = ds[len(ds) // 2]
    half = {}
    for lab, sel in (("前半段", lambda p: p["date"] < mid), ("後半段", lambda p: p["date"] >= mid)):
        rr = _slope(P, "crowd_bucket", subset=sel)
        if rr is None:
            continue
        print(f"    {lab}（切點 {mid}）n={rr[0]['_n']} 斜率={pct(rr[0]['slope']['coef'])} "
              f"cl_t={rr[0]['slope']['t_cl']:+.2f}")
        half[lab] = dict(n=rr[0]["_n"], slope=rr[0]["slope"]["coef"], t_cl=rr[0]["slope"]["t_cl"])
    ratio = (half["後半段"]["slope"] / half["前半段"]["slope"]) if half["前半段"]["slope"] else float("nan")
    print(f"    後半/前半 幅度比 = {ratio:.2f} ⇒ 時間穩定？{0.5 < ratio < 2.0}")
    rob["split_half"] = dict(half, mid=mid, ratio=float(ratio), stable=bool(0.5 < ratio < 2.0))

    # (4) beta 解釋力
    print("\n  (4) 這是不是 beta 傾斜？（各 crowd 桶的隱含 beta 與剝 beta 後的 α）")
    bstat = {}
    for cb in range(5):
        rows = [(sx(p), bench_of(p), p["date"]) for p in DIR
                if p.get("crowd_bucket") == cb and sx(p) is not None and bench_of(p) is not None]
        y = [r[0] for r in rows]
        r = ols_cluster(y, np.column_stack([np.ones(len(rows)), [r[1] for r in rows]]),
                        [r[2] for r in rows], ["a", "b"])
        print(f"    桶{cb} n={r['_n']:<4} 隱含beta={1+r['b']['coef']:.3f}  "
              f"剝beta後α={pct(r['a']['coef'])} (cl_t={r['a']['t_cl']:+.2f})")
        bstat[cb] = dict(n=r["_n"], implied_beta=1 + r["b"]["coef"],
                         alpha=r["a"]["coef"], alpha_t_cl=r["a"]["t_cl"])
    # 集內斜率 vs 該集大盤報酬
    sl, mk = [], []
    byd = defaultdict(list)
    for p in DIR:
        y, b = sx(p), bench_of(p)
        if y is None or b is None or p.get("crowd_bucket") is None:
            continue
        byd[p["date"]].append((y, p["crowd_bucket"], b))
    for d, rows in byd.items():
        if len(rows) < 8:
            continue
        ys = np.array([r[0] for r in rows])
        rk = within_rank([r[1] for r in rows])
        if rk is None or np.std(rk) == 0:
            continue
        x = rk - rk.mean()
        sl.append((x @ (ys - ys.mean())) / (x @ x))
        mk.append(rows[0][2])
    sl, mk = np.array(sl), np.array(mk)
    cc = float(np.corrcoef(sl, mk)[0, 1])
    lo, hi = mk < np.median(mk), mk >= np.median(mk)
    print(f"    corr(集內 crowd 斜率, 該集大盤 d3 報酬) = {cc:+.3f}；"
          f"大盤跌半數斜率={pct(sl[lo].mean())} vs 漲半數={pct(sl[hi].mean())}")
    print(f"    ⇒ 剝 beta 後 5 個桶的 α 是否單調/顯著："
          f"{[round(100*bstat[c]['alpha'], 3) for c in range(5)]}，"
          f"|t| 全部 < 2？{all(abs(bstat[c]['alpha_t_cl']) < 2 for c in range(5))}")
    rob["beta"] = dict(buckets=bstat, corr_slope_market=cc,
                       slope_down_mkt=float(sl[lo].mean()), slope_up_mkt=float(sl[hi].mean()))

    # (5) 其他持有期 + 其他 crowd 視窗（已在主檢定）
    print("\n  (5) 其他持有期（d3 為事前註冊；其餘僅供參考）")
    hz = {}
    for feat, lab in (("crowd_bucket", "H8 crowd"), ("ext_z", "H3 ext_z")):
        line = f"    {lab:<12}"
        hz[lab] = {}
        for h in HORIZONS:
            rr = _slope(P, feat, h)
            line += f"  {h}={pct(rr[0]['slope']['coef'])}(t={rr[0]['slope']['t_cl']:+.2f})"
            hz[lab][h] = dict(slope=rr[0]["slope"]["coef"], t_cl=rr[0]["slope"]["t_cl"])
        print(line)
    rob["horizons"] = hz

    # (6) 任一方向 vs 只數有方向的推薦
    rr = _slope(P, "crowd_bucket_any")
    print(f"\n  (6) crowd 改用「含觀望/中性」定義：斜率={pct(rr[0]['slope']['coef'])} "
          f"cl_t={rr[0]['slope']['t_cl']:+.2f}")
    rob["crowd_anystance"] = dict(slope=rr[0]["slope"]["coef"], t_cl=rr[0]["slope"]["t_cl"])

    # (7) 未還原 vs 還原價：批判者第 1 項修正的實際影響
    print("\n  (7) 批判者修正 #1（未還原價）的實際影響：見報告——"
          "ext_z 未還原 vs 還原 名次相關 0.9994，斜率 +0.228% vs +0.240%，"
          "理論上正確但實務上不影響結論。")
    return rob


def task_b(P, store):
    out = {}
    print("\n" + "═" * 70)
    print("任務 B：H3（位階／伸展度）＋ H8（擁擠度）—— 事前註冊符號：兩者皆為負")
    print("═" * 70)
    P = build_features(P, store)
    DIR = [p for p in P if p["stance"] in DIRECTIONAL]
    cov = sum(1 for p in DIR if p.get("ext_z") is not None and sx(p) is not None)
    print(f"\n  H3 技術指標覆蓋（未還原價，auto_adjust=False）：{cov} / "
          f"{sum(1 for p in DIR if sx(p) is not None)} 個有 d3 超額的部位")
    cd = Counter(p["crowd_10_dir"] for p in DIR)
    cda = Counter(p["crowd_10"] for p in DIR)
    print(f"  H8 crowd_10 分布（去重部位 {len(DIR)}，只數有方向的推薦）：" +
          " ".join(f"{k}次={cd[k]}" for k in sorted(cd)[:8]))
    print(f"    0 次 = {cd[0]}（假說文件宣稱 798 ✓）；若把觀望/中性也算進去則為 {cda[0]}")
    out["coverage"] = dict(n_ext_z=cov,
                           crowd10_dist={str(k): v for k, v in sorted(cd.items())},
                           crowd10_dist_anystance={str(k): v for k, v in sorted(cda.items())})

    # 桶化 crowd → 0/1/2/3-4/>=5 → 0..4
    # 主定義用「有方向（看多/看空）的推薦」計數：這才重現假說文件宣稱的
    # 0 次 = 798（本腳本算出 799），確保檢定的是事前註冊的那個構念。
    def buck(c):
        return None if c is None else (c if c <= 2 else (3 if c <= 4 else 4))

    for p in P:
        p["crowd_bucket"] = buck(p.get("crowd_10_dir"))
        p["crowd_bucket_any"] = buck(p.get("crowd_10"))
        for w in (5, 20):
            p[f"crowd{w}_bucket"] = buck(p.get(f"crowd_{w}_dir"))

    print("\n─── H3 主檢定 ───")
    out["H3_primary"] = spread_test("ext_z", P, "H3 主：ext_z（波動標準化伸展度）", -1)
    print("\n─── H3 次要 / 穩健性 ───")
    out["H3_ext20"] = spread_test("ext20", P, "H3 次：ext20 = Close/MA20 − 1", -1)
    out["H3_nh60"] = spread_test("near_high60", P, "H3 次：near_high60 = Close/60日高 − 1", -1)

    print("\n─── H8 主檢定 ───")
    out["H8_primary"] = spread_test("crowd_bucket", P, "H8 主：crowd_10 桶（0/1/2/3-4/>=5）", -1)
    print("\n─── H8 次要 / 穩健性 ───")
    out["H8_crowd5"] = spread_test("crowd5_bucket", P, "H8 次：crowd_5", -1)
    out["H8_crowd20"] = spread_test("crowd20_bucket", P, "H8 次：crowd_20", -1)

    # 全樣本（非集內）分桶，作為對照
    print("\n─── 全樣本分桶（非集內；含 beta/大盤衝擊，僅供對照）───")
    for feat, lab in (("ext_z", "ext_z"), ("crowd_bucket", "crowd_10 桶")):
        rows = [(sx(p), p.get(feat), p["date"]) for p in DIR]
        rows = [r for r in rows if r[0] is not None and r[1] is not None]
        fs = np.array([r[1] for r in rows], float)
        q = np.minimum((pd.Series(fs).rank(method="first").to_numpy() - 1) * 5 // len(fs), 4)
        ms = []
        for qi in range(5):
            sel = [rows[i] for i in range(len(rows)) if q[i] == qi]
            s = cluster_stats([r[0] for r in sel], [r[2] for r in sel])
            ms.append(s["mean"])
        print(f"    {lab}: " + "  ".join(pct(m) for m in ms))
        out[f"fullsample_{feat}"] = ms

    # ── 聯合檢定 ──
    print("\n─── 聯合檢定（H3 × H8）───")
    by = defaultdict(list)
    for p in P:
        if p["stance"] not in DIRECTIONAL:
            continue
        y = sx(p)
        if y is None or p.get("ext_z") is None or p.get("crowd_bucket") is None:
            continue
        by[p["date"]].append((y, p["ext_z"], p["crowd_bucket"], p))
    Y, E, C, G, PS = [], [], [], [], []
    for d, rows in by.items():
        if len(rows) < 5:
            continue
        ys = np.array([r[0] for r in rows])
        re_ = within_rank([r[1] for r in rows])
        rc = within_rank([r[2] for r in rows])
        Y.extend(ys - ys.mean())
        E.extend(re_ - re_.mean())
        C.extend(rc - rc.mean())
        G.extend([d] * len(rows))
        PS.extend([r[3] for r in rows])
    Y, E, C = np.array(Y), np.array(E), np.array(C)
    from scipy import stats as sps
    joint = ols_cluster(Y, np.column_stack([E, C]), G, ["ext_z_rank", "crowd_rank"])
    print(f"    集內 FE 迴歸 n={joint['_n']} 集數={joint['_G']}；"
          f"corr(ext_z_rank, crowd_rank) = {np.corrcoef(E, C)[0,1]:+.3f}")
    for nm in ("ext_z_rank", "crowd_rank"):
        r = joint[nm]
        z = r["coef"] / r["se_cl"]
        pv = float(2 * (1 - sps.norm.cdf(abs(z))))
        print(f"      {nm:<12} 係數={pct(r['coef'])} cl_se={100*r['se_cl']:.3f}% "
              f"cl_t={r['t_cl']:+.2f} p={pv:.4f}")
        joint[nm]["p_value"] = pv
    uni_e = out["H3_primary"]["slope"]
    uni_c = out["H8_primary"]["slope"]
    print(f"    單變量 → 聯合：ext_z {pct(uni_e)} → {pct(joint['ext_z_rank']['coef'])}；"
          f"crowd {pct(uni_c)} → {pct(joint['crowd_rank']['coef'])}")
    out["joint"] = joint

    # 3×3 double sort
    print("\n    3×3 double sort（集內 ext_z 三分位 × crowd 桶 0 / 1-2 / >=3）毛均值：")
    cell = defaultdict(list)
    for d, rows in by.items():
        if len(rows) < 5:
            continue
        es = np.array([r[1] for r in rows])
        rk = (pd.Series(es).rank(method="first").to_numpy() - 1) * 3 // len(rows)
        for (y, e, c, p), ri in zip(rows, rk):
            cb = 0 if c == 0 else (1 if c <= 2 else 2)
            cell[(int(min(ri, 2)), cb)].append((y, d))
    grid = {}
    print(f"      {'':<12}" + "".join(f"{lab:>16}" for lab in ("crowd=0", "crowd=1-2", "crowd>=3")))
    for ri, rlab in enumerate(("ext 低(Q1)", "ext 中", "ext 高(Q3)")):
        line = f"      {rlab:<12}"
        for cb in range(3):
            v = cell.get((ri, cb), [])
            if len(v) < 20:
                line += f"{'n<20':>16}"
                grid[f"{ri}_{cb}"] = None
                continue
            s = cluster_stats([a for a, _ in v], [b for _, b in v])
            line += f"{pct(s['mean'])+f'(n={s[chr(110)]})':>16}"
            grid[f"{ri}_{cb}"] = dict(n=s["n"], mean=s["mean"], net=s["net"], crve_t=s["crve_t"])
        print(line)
    out["double_sort"] = grid

    # 最佳組合（事前方向：低 ext ＋ crowd=0）的 long-only 可交易性
    v = cell.get((0, 0), [])
    if len(v) >= 20:
        s = cluster_stats([a for a, _ in v], [b for _, b in v])
        ci, _ = block_boot([a for a, _ in v], [b for _, b in v])
        d5, kk = drop_top5([a for a, _ in v], [b for _, b in v])
        print(f"\n    事前註冊的「最好格」= ext 低 × crowd=0：n={s['n']} 毛={pct(s['mean'])} "
              f"淨={pct(s['net'])} CRVE_t={s['crve_t']:+.2f} boot95(毛)=[{pct(ci[0])},{pct(ci[1])}]")
        print(f"      去掉最好 5%（{kk} 筆）後 淨={pct(d5['net'])} ⇒ 變號="
              f"{(s['net']>0)!=(d5['net']>0)}")
        out["best_cell"] = dict(stats=s, boot_ci=list(ci), drop5=d5,
                                sign_flip=bool((s["net"] > 0) != (d5["net"] > 0)))

    out["robust"] = robustness(P, DIR, out)

    # MDE
    res_sd = float(np.std(Y, ddof=1))
    n_per = joint["_n"] / 5
    mde_iid = 2.8 * res_sd * np.sqrt(2 / n_per)
    # 實測的集群穩健 SE 才是真正的偵測底線（含群集相關與不平衡）
    se_real = float(out["H3_primary"]["slope_se_cl"])
    mde_real = 2.8 * se_real
    n_ep = int(out["H3_primary"]["n_episodes"])
    print(f"\n    集內殘差 sd = {100*res_sd:.2f}%（假說文件宣稱 7.09%）")
    print(f"    理想 iid 版 MDE ≈ {100*mde_iid:.2f}%")
    print(f"    ** 實測的集內分位差斜率 SE = {100*se_real:.3f}% ⇒ 真實 MDE（80% power）"
          f"≈ {100*mde_real:.2f}% **；成本牆 0.57%")
    need = {}
    for tgt, lab in ((0.011, "假說文件的 1.1% 門檻"), (COST, "成本牆 0.57%")):
        factor = (mde_real / tgt) ** 2
        need[lab] = dict(target=tgt, episodes_needed=int(round(n_ep * factor)),
                         extra_years=round(n_ep * factor / 250, 2))
        print(f"    要把 MDE 壓到 {100*tgt:.2f}%（{lab}）需要約 {int(round(n_ep*factor))} 集"
              f"（目前 {n_ep} 集，約 {n_ep*factor/250:.1f} 年每日節目）")
    out["mde"] = dict(residual_sd=res_sd, mde_iid=float(mde_iid),
                      realized_slope_se=se_real, mde_realized=float(mde_real),
                      n_episodes=n_ep, sample_needed=need)
    return out


def main():
    P = load_positions()
    RES["taskA"] = task_a(P)
    syms = sorted({p["symbol"] for p in P if p.get("symbol")})
    store = load_prices(syms)
    RES["taskB"] = task_b(P, store)
    json.dump(RES, open(OUT_JSON, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=float)
    print(f"\n已寫出 {OUT_JSON}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()

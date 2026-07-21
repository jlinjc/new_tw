# -*- coding: utf-8 -*-
"""彙整 analysis + performance 資料，產生單一檔案 dashboard HTML。

輸出 output/dashboard.html，直接用瀏覽器開啟。
"""
import json
import re
import sys
from datetime import datetime

from config import ANALYSIS_DIR, DASHBOARD_HTML, PERFORMANCE_JSON

HORIZONS = ["d3", "w1", "w2", "m1"]
HORIZON_LABELS = {"d3": "+3日", "w1": "+1週", "w2": "+2週", "m1": "+1月"}


def load_data():
    perf_list = (json.loads(PERFORMANCE_JSON.read_text(encoding="utf-8"))
                 if PERFORMANCE_JSON.exists() else [])
    perf_map = {}
    for r in perf_list:
        key = (r["video_id"], r["teacher"], r["stock_name"])
        perf_map[key] = r

    episodes = []
    for f in sorted(ANALYSIS_DIR.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        v = data["video"]
        ep = {
            "id": v["id"], "title": v["title"], "date": v["date"],
            "url": v["url"],
            "summary": data.get("episode_summary", ""),
            "market_view": data.get("market_view", ""),
            "teachers": [],
        }
        for t in data.get("teachers", []):
            picks = []
            for p in t.get("picks", []):
                rec = dict(p)
                pm = perf_map.get((v["id"], t["name"], p["stock_name"]))
                rec["perf"] = pm.get("perf") if pm else None
                rec["excess"] = pm.get("excess") if pm else None
                rec["bench"] = pm.get("bench") if pm else None
                rec["hit"] = pm.get("hit") if pm else {}
                picks.append(rec)
            ep["teachers"].append({"name": t["name"], "picks": picks})
        episodes.append(ep)
    episodes.sort(key=lambda e: e["date"], reverse=True)
    return episodes


def wilson_ci(hits: int, n: int, z: float = 1.96):
    """命中率的 Wilson 95% 信賴區間，回傳 [lo, hi]。"""
    if not n:
        return None
    p = hits / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return [round(max(0.0, center - half), 4), round(min(1.0, center + half), 4)]


def compute_teacher_stats(episodes):
    stats = {}
    for ep in episodes:
        for t in ep["teachers"]:
            s = stats.setdefault(t["name"], {
                "name": t["name"], "n_picks": 0, "n_scored": 0,
                "episodes": set(),
                "h": {h: {"n": 0, "hits": 0, "sum_ret": 0.0, "sum_ex": 0.0,
                          "n_ex": 0} for h in HORIZONS},
            })
            if t["picks"]:
                s["episodes"].add(ep["id"])
            for p in t["picks"]:
                s["n_picks"] += 1
                # 只有明確多空方向的推薦才計分；中性/觀望僅展示
                if p["stance"] not in ("看多", "看空"):
                    continue
                perf = p.get("perf")
                if not perf:
                    continue
                s["n_scored"] += 1
                sign = -1 if p["stance"] == "看空" else 1
                for h in HORIZONS:
                    r = perf["ret"].get(h)
                    if r is None:
                        continue
                    hh = s["h"][h]
                    hh["n"] += 1
                    hh["sum_ret"] += sign * r
                    if sign * r > 0:
                        hh["hits"] += 1
                    ex = (p.get("excess") or {}).get(h)
                    if ex is not None:
                        hh["sum_ex"] += sign * ex
                        hh["n_ex"] += 1
    out = []
    for s in stats.values():
        row = {"name": s["name"], "n_picks": s["n_picks"],
               "n_scored": s["n_scored"],
               "n_episodes": len(s["episodes"]), "h": {}}
        for h in HORIZONS:
            hh = s["h"][h]
            row["h"][h] = {
                "n": hh["n"],
                "hit_rate": round(hh["hits"] / hh["n"], 4) if hh["n"] else None,
                "hit_ci": wilson_ci(hh["hits"], hh["n"]),
                "avg_ret": round(hh["sum_ret"] / hh["n"], 5) if hh["n"] else None,
                "avg_excess": (round(hh["sum_ex"] / hh["n_ex"], 5)
                               if hh["n_ex"] else None),
            }
        out.append(row)
    out.sort(key=lambda r: -(r["h"]["m1"]["avg_excess"] or -9e9))
    return out


def compute_teacher_curves(episodes, top_n: int = 6):
    """每位老師「等權跟單」的逐筆超額報酬序列（依進場日排序）。

    只納入有多空方向且有股價資料的推薦；看空以反向計。
    回傳樣本數最多的前 top_n 位老師。
    """
    series = {}
    for ep in episodes:
        for t in ep["teachers"]:
            for p in t["picks"]:
                if p["stance"] not in ("看多", "看空"):
                    continue
                perf = p.get("perf")
                if not perf or not p.get("excess"):
                    continue
                sign = -1 if p["stance"] == "看空" else 1
                point = {
                    "date": perf["entry_date"],
                    "stock": p["stock_name"],
                    "ex": {h: (round(sign * p["excess"][h], 5)
                               if p["excess"].get(h) is not None else None)
                           for h in HORIZONS},
                }
                series.setdefault(t["name"], []).append(point)
    curves = [{"name": name, "points": sorted(pts, key=lambda x: x["date"])}
              for name, pts in series.items()]
    curves.sort(key=lambda c: -len(c["points"]))
    return curves[:top_n]


def _has_symbol(p) -> bool:
    """與 performance.yahoo_symbol 一致：這筆推薦是否能組出報價代碼。"""
    tk = p.get("ticker") or ""
    if p.get("market") in ("TW", "ETF") and re.fullmatch(r"\d{4,6}[A-Z]{0,2}", tk):
        return True
    return p.get("market") == "US" and bool(tk) and tk.replace(".", "").isalpha()


def compute_quality(episodes):
    """資料品質面板：無法對應代號、低歸屬信心、抓不到股價的清單。"""
    items = []
    for ep in episodes:
        for t in ep["teachers"]:
            for p in t["picks"]:
                issues = []
                if (p.get("market") in ("TW", "ETF")
                        and not (p.get("ticker") and p.get("tw_market"))):
                    issues.append("no_ticker")
                if p.get("confidence") == "low":
                    issues.append("low_conf")
                if _has_symbol(p) and not p.get("perf"):
                    issues.append("no_price")
                if not issues:
                    continue
                items.append({
                    "video_id": ep["id"], "date": ep["date"],
                    "teacher": t["name"], "stock_name": p["stock_name"],
                    "ticker": p.get("ticker"), "market": p.get("market"),
                    "stance": p["stance"], "confidence": p.get("confidence"),
                    "quote": p.get("quote", ""), "issues": issues,
                })
    items.sort(key=lambda x: x["date"], reverse=True)
    counts = {k: sum(1 for it in items if k in it["issues"])
              for k in ("no_ticker", "low_conf", "no_price")}
    return {"items": items, "counts": counts}


def compute_stock_stats(episodes):
    stocks = {}
    for ep in episodes:
        for t in ep["teachers"]:
            for p in t["picks"]:
                key = p.get("ticker") or p["stock_name"]
                s = stocks.setdefault(key, {
                    "stock_name": p["stock_name"], "ticker": p.get("ticker"),
                    "n": 0, "bull": 0, "bear": 0, "teachers": set(),
                    "last_date": "", "rets_m1": [], "ex_m1": [],
                })
                s["n"] += 1
                if p["stance"] == "看多":
                    s["bull"] += 1
                if p["stance"] == "看空":
                    s["bear"] += 1
                s["teachers"].add(t["name"])
                s["last_date"] = max(s["last_date"], ep["date"])
                if p.get("perf") and p["perf"]["ret"].get("m1") is not None:
                    s["rets_m1"].append(p["perf"]["ret"]["m1"])
                ex = (p.get("excess") or {}).get("m1")
                if ex is not None:
                    s["ex_m1"].append(ex)
    out = []
    for s in stocks.values():
        out.append({
            "stock_name": s["stock_name"], "ticker": s["ticker"], "n": s["n"],
            "bull": s["bull"], "bear": s["bear"],
            "teachers": sorted(s["teachers"]), "last_date": s["last_date"],
            "avg_ret_m1": (round(sum(s["rets_m1"]) / len(s["rets_m1"]), 5)
                           if s["rets_m1"] else None),
            "avg_ex_m1": (round(sum(s["ex_m1"]) / len(s["ex_m1"]), 5)
                          if s["ex_m1"] else None),
        })
    out.sort(key=lambda r: (-r["n"], r["last_date"]))
    return out


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>理財達人秀 老師推薦追蹤</title>
<style>
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb;
  --ink: #0b0b0b; --ink2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --baseline: #c3c2b7;
  --border: rgba(11,11,11,0.10);
  --up: #d03b3b; --down: #0ca30c;      /* 台股慣例：紅漲綠跌 */
  --up-bg: rgba(208,59,59,0.10); --down-bg: rgba(12,163,12,0.10);
  --accent: #2a78d6; --accent2: #1c5cab;
  --chip-bull: #d03b3b; --chip-bear: #0ca30c; --chip-neutral: #898781;
  --s1: #2a78d6; --s2: #008300; --s3: #e87ba4;
  --s4: #eda100; --s5: #1baf7a; --s6: #eb6834;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19;
    --ink: #ffffff; --ink2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --baseline: #383835;
    --border: rgba(255,255,255,0.10);
    --up: #e66767; --down: #0ca30c;
    --up-bg: rgba(230,103,103,0.14); --down-bg: rgba(12,163,12,0.14);
    --accent: #3987e5; --accent2: #86b6ef;
    --s1: #3987e5; --s2: #008300; --s3: #d55181;
    --s4: #c98500; --s5: #199e70; --s6: #d95926;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19;
  --ink: #ffffff; --ink2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --baseline: #383835;
  --border: rgba(255,255,255,0.10);
  --up: #e66767; --down: #0ca30c;
  --up-bg: rgba(230,103,103,0.14); --down-bg: rgba(12,163,12,0.14);
  --accent: #3987e5; --accent2: #86b6ef;
  --s1: #3987e5; --s2: #008300; --s3: #d55181;
  --s4: #c98500; --s5: #199e70; --s6: #d95926;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink);
  font-family: system-ui, -apple-system, "Segoe UI", "Microsoft JhengHei", sans-serif;
  font-size: 14px; line-height: 1.55; }
.wrap { max-width: 1080px; margin: 0 auto; padding: 20px 16px 60px; }
h1 { font-size: 20px; margin: 0 0 4px; }
.sub { color: var(--muted); font-size: 12px; margin-bottom: 16px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px,1fr));
  gap: 10px; margin-bottom: 18px; }
.tile { background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 12px 14px; }
.tile .v { font-size: 24px; font-weight: 650; }
.tile .k { color: var(--ink2); font-size: 12px; }
.tabs { display: flex; gap: 6px; margin-bottom: 14px; flex-wrap: wrap; }
.tab { padding: 7px 14px; border-radius: 8px; border: 1px solid var(--border);
  background: var(--surface); color: var(--ink2); cursor: pointer; font-size: 13px; }
.tab.active { background: var(--accent); border-color: var(--accent);
  color: #fff; font-weight: 600; }
.card { background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; padding: 14px 16px; margin-bottom: 10px; }
.ep-row { cursor: pointer; }
.ep-row:hover { border-color: var(--accent); }
.ep-date { color: var(--muted); font-size: 12px; }
.ep-title { font-weight: 600; margin: 2px 0 6px; }
.badges { display: flex; flex-wrap: wrap; gap: 6px; }
.badge { font-size: 12px; padding: 2px 8px; border-radius: 999px;
  border: 1px solid var(--border); color: var(--ink2); }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th { text-align: left; color: var(--muted); font-weight: 500;
  border-bottom: 1px solid var(--baseline); padding: 6px 8px; white-space: nowrap; }
td { border-bottom: 1px solid var(--grid); padding: 6px 8px; vertical-align: top; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tr.clickable { cursor: pointer; }
tr.clickable:hover td { background: rgba(42,120,214,0.06); }
.pos { color: var(--up); } .neg { color: var(--down); }
.chip { display: inline-block; font-size: 12px; font-weight: 600;
  padding: 1px 8px; border-radius: 999px; color: #fff; }
.chip.bull { background: var(--chip-bull); }
.chip.bear { background: var(--chip-bear); }
.chip.neutral, .chip.watch { background: var(--chip-neutral); }
.hit { font-weight: 700; }
.hit.y { color: var(--up); } .hit.n { color: var(--down); }
.pick { border-top: 1px solid var(--grid); padding: 10px 0; }
.pick:first-child { border-top: none; }
.pick h4 { margin: 0 0 4px; font-size: 14px; }
.pick .meta { color: var(--ink2); font-size: 12.5px; margin-bottom: 4px; }
.reasons { margin: 4px 0 6px 0; padding-left: 18px; color: var(--ink); }
.reasons li { margin: 1px 0; }
.quote { color: var(--muted); font-size: 12.5px; border-left: 3px solid var(--grid);
  padding-left: 8px; margin: 4px 0; }
.perf-grid { display: inline-grid; grid-template-columns: repeat(4, minmax(74px, auto));
  gap: 2px; margin-top: 4px; }
.perf-cell { background: var(--page); border: 1px solid var(--grid);
  border-radius: 6px; padding: 3px 8px; text-align: center; }
.perf-cell .h { font-size: 11px; color: var(--muted); }
.perf-cell .r { font-weight: 650; font-variant-numeric: tabular-nums; }
.perf-cell .e { font-size: 11px; color: var(--ink2);
  font-variant-numeric: tabular-nums; }
.back { color: var(--accent); cursor: pointer; font-size: 13px;
  margin-bottom: 10px; display: inline-block; }
.teacher-name { color: var(--accent); font-weight: 650; }
.bar-wrap { display: flex; align-items: center; gap: 6px; }
.bar-track { position: relative; width: 120px; height: 10px; }
.bar-zero { position: absolute; left: 50%; top: -2px; bottom: -2px;
  width: 1px; background: var(--baseline); }
.bar { position: absolute; top: 0; height: 10px; border-radius: 0 4px 4px 0; }
.bar.p { left: 50%; background: var(--up); }
.bar.m { right: 50%; background: var(--down); border-radius: 4px 0 0 4px; }
.note { color: var(--muted); font-size: 12px; margin: 8px 0 14px; }
a { color: var(--accent); }
.search { width: 100%; max-width: 320px; padding: 7px 10px; border-radius: 8px;
  border: 1px solid var(--border); background: var(--surface); color: var(--ink);
  margin-bottom: 10px; font-size: 13px; }
.theme-toggle { float: right; cursor: pointer; border: 1px solid var(--border);
  background: var(--surface); color: var(--ink2); border-radius: 8px;
  padding: 5px 10px; font-size: 12px; }
.chart-head { display: flex; align-items: center; justify-content: space-between;
  gap: 10px; flex-wrap: wrap; margin-bottom: 6px; }
.chart-title { font-weight: 650; font-size: 14px; }
.seg { display: inline-flex; gap: 2px; background: var(--page);
  border: 1px solid var(--border); border-radius: 8px; padding: 2px; }
.seg button { border: none; background: transparent; color: var(--ink2);
  font-size: 12px; padding: 3px 10px; border-radius: 6px; cursor: pointer; }
.seg button.on { background: var(--accent); color: #fff; font-weight: 600; }
.legend { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 12px;
  color: var(--ink2); margin: 4px 0 8px; }
.legend .it { display: inline-flex; align-items: center; gap: 5px; }
.legend .sw { width: 14px; height: 3px; border-radius: 2px; }
.viz { position: relative; }
.viz svg { width: 100%; height: auto; display: block; }
.viz .tip { position: absolute; pointer-events: none; z-index: 5;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 6px 10px; font-size: 12px; display: none;
  box-shadow: 0 2px 12px rgba(0,0,0,0.15); white-space: nowrap; }
.viz .tip .row { display: flex; align-items: center; gap: 5px; }
.viz .tip .num { margin-left: auto; padding-left: 12px;
  font-variant-numeric: tabular-nums; }
.ci { font-size: 10.5px; color: var(--muted); font-variant-numeric: tabular-nums; }
.sig { font-weight: 700; }
.issue { display: inline-block; font-size: 11px; padding: 1px 7px;
  border-radius: 999px; border: 1px solid var(--border); color: var(--ink2);
  white-space: nowrap; margin: 1px 2px 1px 0; }
.qquote { color: var(--muted); font-size: 12px; max-width: 460px; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }
.chipbtn { padding: 4px 12px; border-radius: 999px; font-size: 12px;
  border: 1px solid var(--border); background: var(--surface);
  color: var(--ink2); cursor: pointer; }
.chipbtn.on { background: var(--accent); border-color: var(--accent);
  color: #fff; font-weight: 600; }
</style>
</head>
<body>
<div class="wrap">
  <button class="theme-toggle" onclick="toggleTheme()">深/淺色</button>
  <h1>理財達人秀 老師推薦追蹤</h1>
  <div class="sub">資料更新：__GENERATED__ ｜ 進場點＝播出日後首個交易日收盤 ｜ 命中＝依多空方向調整後報酬 &gt; 0 ｜ 超額＝相對加權指數</div>
  <div class="tiles" id="tiles"></div>
  <div class="tabs">
    <button class="tab active" data-view="episodes" onclick="switchTab(this)">集數列表</button>
    <button class="tab" data-view="teachers" onclick="switchTab(this)">老師排行</button>
    <button class="tab" data-view="stocks" onclick="switchTab(this)">個股彙總</button>
    <button class="tab" data-view="quality" onclick="switchTab(this)">資料品質</button>
  </div>
  <div id="content"></div>
</div>
<script>
const DATA = __DATA__;
const HORIZONS = ["d3","w1","w2","m1"];
const HL = {d3:"+3日", w1:"+1週", w2:"+2週", m1:"+1月"};
let view = "episodes", currentEp = null;

function toggleTheme() {
  const r = document.documentElement;
  const cur = r.dataset.theme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  r.dataset.theme = cur === 'dark' ? 'light' : 'dark';
}
function esc(s) { return (s||"").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function pct(x, digits=1) {
  if (x === null || x === undefined) return "–";
  const v = (x*100).toFixed(digits);
  return (x>0? "+":"") + v + "%";
}
function cls(x) { return x===null||x===undefined ? "" : (x>0 ? "pos" : x<0 ? "neg" : ""); }
function stanceChip(s) {
  const m = {"看多":"bull","看空":"bear","中性":"neutral","觀望":"watch"};
  return `<span class="chip ${m[s]||'neutral'}">${esc(s)}</span>`;
}

function renderTiles() {
  const eps = DATA.episodes;
  let nPicks = 0, teachers = new Set(), nMatured = 0;
  eps.forEach(e => e.teachers.forEach(t => {
    if (t.picks.length) teachers.add(t.name);
    t.picks.forEach(p => { nPicks++;
      if (p.perf && p.perf.ret.m1 !== null &&
          (p.stance === "看多" || p.stance === "看空")) nMatured++; });
  }));
  document.getElementById("tiles").innerHTML = [
    ["集數", eps.length], ["老師", teachers.size],
    ["推薦筆數", nPicks], ["滿一個月可評分", nMatured],
  ].map(([k,v]) => `<div class="tile"><div class="v">${v}</div><div class="k">${k}</div></div>`).join("");
}

function switchTab(btn) {
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
  btn.classList.add("active");
  view = btn.dataset.view; currentEp = null;
  render();
}

function render() {
  const el = document.getElementById("content");
  if (view === "episodes") el.innerHTML = currentEp ? epDetail(currentEp) : epList();
  else if (view === "teachers") { el.innerHTML = curveSection() + teacherTable(); wireCurveHover(); }
  else if (view === "quality") el.innerHTML = qualityView();
  else el.innerHTML = stockTable();
}

function epList() {
  return DATA.episodes.map(e => {
    const badges = e.teachers.filter(t=>t.picks.length).map(t =>
      `<span class="badge">${esc(t.name)} ×${t.picks.length}</span>`).join("");
    return `<div class="card ep-row" onclick="openEp('${e.id}')">
      <div class="ep-date">${e.date}</div>
      <div class="ep-title">${esc(e.title)}</div>
      <div class="badges">${badges}</div>
    </div>`;
  }).join("") || `<div class="note">尚無分析資料。</div>`;
}

function openEp(id) {
  currentEp = DATA.episodes.find(e => e.id === id);
  render();
  window.scrollTo(0, 0);
}

function perfCells(p) {
  if (!p.perf) return `<div class="note" style="margin:4px 0">無股價資料（美股/未識別代號/太新）</div>`;
  return `<div class="perf-grid">` + HORIZONS.map(h => {
    const r = p.perf.ret[h], ex = p.excess ? p.excess[h] : null;
    const hit = p.hit ? p.hit[h] : null;
    const hitMark = hit === true ? `<span class="hit y">✓</span>` : hit === false ? `<span class="hit n">✗</span>` : "";
    return `<div class="perf-cell"><div class="h">${HL[h]} ${hitMark}</div>
      <div class="r ${cls(r)}">${pct(r)}</div>
      <div class="e">超額 ${pct(ex)}</div></div>`;
  }).join("") + `</div>`;
}

function epDetail(e) {
  let html = `<span class="back" onclick="currentEp=null;render()">← 回集數列表</span>`;
  html += `<div class="card"><div class="ep-date">${e.date}</div>
    <div class="ep-title">${esc(e.title)}</div>
    <div>${esc(e.summary)}</div>
    <div class="note">大盤看法：${esc(e.market_view)}</div>
    <a href="${e.url}" target="_blank">在 YouTube 開啟 ↗</a></div>`;
  e.teachers.forEach(t => {
    if (!t.picks.length) return;
    html += `<div class="card"><h3 style="margin:0 0 8px"><span class="teacher-name">${esc(t.name)}</span> 老師</h3>`;
    t.picks.forEach(p => {
      const entry = p.perf ? `進場 ${p.perf.entry_date} @ ${p.perf.entry_price}` : "";
      html += `<div class="pick">
        <h4>${esc(p.stock_name)} ${p.ticker ? `(${p.ticker})` : ""} ${stanceChip(p.stance)}</h4>
        <div class="meta">${p.action ? "建議：" + esc(p.action) : ""} ${p.target_price ? "｜目標/關卡：" + esc(p.target_price) : ""} ${entry ? "｜" + entry : ""}</div>
        <ul class="reasons">${p.reasons.map(r => `<li>${esc(r)}</li>`).join("")}</ul>
        ${p.quote ? `<div class="quote">「${esc(p.quote)}」</div>` : ""}
        ${perfCells(p)}
      </div>`;
    });
    html += `</div>`;
  });
  return html;
}

function bar(x, maxAbs) {
  if (x === null || x === undefined) return "–";
  const w = Math.min(Math.abs(x)/maxAbs*60, 60);
  const b = x >= 0 ? `<div class="bar p" style="width:${w}px"></div>`
                   : `<div class="bar m" style="width:${w}px"></div>`;
  return `<div class="bar-wrap"><div class="bar-track"><div class="bar-zero"></div>${b}</div>
    <span class="${cls(x)}">${pct(x)}</span></div>`;
}

// ---- 累積超額報酬曲線（等權跟單） ----
let curveH = "m1";
const SCOLORS = ["var(--s1)","var(--s2)","var(--s3)","var(--s4)","var(--s5)","var(--s6)"];

function setCurveH(h) { curveH = h; render(); }

function curveData(h) {
  return (DATA.teacherCurves || []).map((c, i) => {
    let cum = 0; const pts = [];
    c.points.forEach(p => {
      const e = p.ex[h];
      if (e === null || e === undefined) return;
      cum += e;
      pts.push({x: Date.parse(p.date), y: cum, date: p.date, stock: p.stock});
    });
    return {name: c.name, color: SCOLORS[i % SCOLORS.length], pts};
  }).filter(s => s.pts.length >= 2);
}

function niceTicks(lo, hi, n) {
  const span = hi - lo || 1;
  const mag = Math.pow(10, Math.floor(Math.log10(span / n)));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => span / s <= n) || 10 * mag;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-12; v += step) out.push(v);
  return out;
}

function monthTicks(x0, x1) {
  const out = []; const d = new Date(x0); d.setDate(1); d.setHours(0,0,0,0);
  if (d.getTime() < x0) d.setMonth(d.getMonth() + 1);
  while (d.getTime() <= x1) {
    out.push({x: d.getTime(), label: (d.getMonth() + 1) + "月"});
    d.setMonth(d.getMonth() + 1);
  }
  return out;
}

function curveSection() {
  const series = curveData(curveH);
  if (!series.length) return "";
  const W = 920, H = 300, L = 52, R = 118, T = 14, B = 30;
  const xs = series.flatMap(s => s.pts.map(p => p.x));
  const ally = series.flatMap(s => s.pts.map(p => p.y)).concat([0]);
  let x0 = Math.min(...xs), x1 = Math.max(...xs);
  const DAY = 864e5;
  if (x1 - x0 < 7 * DAY) {   // 日期太集中時加寬，避免全部貼在左軸
    const mid = (x0 + x1) / 2;
    x0 = mid - 7 * DAY; x1 = mid + 7 * DAY;
  }
  let y0 = Math.min(...ally), y1 = Math.max(...ally);
  const padY = (y1 - y0) * 0.08 || 0.01; y0 -= padY; y1 += padY;
  const X = v => L + (v - x0) / (x1 - x0 || 1) * (W - L - R);
  const Y = v => T + (y1 - v) / (y1 - y0 || 1) * (H - T - B);
  window._curve = {series, W, H, L, R, T, B, x0, x1, y0, y1};

  let g = "";
  niceTicks(y0, y1, 5).forEach(v => {
    g += `<line x1="${L}" x2="${W-R}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}" stroke="var(--grid)" stroke-width="1"/>` +
         `<text x="${L-8}" y="${(Y(v)+4).toFixed(1)}" text-anchor="end" font-size="11" fill="var(--muted)">${(v*100).toFixed(Math.abs(v)<0.05?1:0)}%</text>`;
  });
  if (y0 < 0 && y1 > 0)
    g += `<line x1="${L}" x2="${W-R}" y1="${Y(0).toFixed(1)}" y2="${Y(0).toFixed(1)}" stroke="var(--baseline)" stroke-width="1.5"/>`;
  monthTicks(x0, x1).forEach(t => {
    g += `<text x="${X(t.x).toFixed(1)}" y="${H-8}" text-anchor="middle" font-size="11" fill="var(--muted)">${t.label}</text>`;
  });
  series.forEach(s => {
    const d = s.pts.map((p, j) => `${j ? "L" : "M"}${X(p.x).toFixed(1)} ${Y(p.y).toFixed(1)}`).join("");
    g += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
  });
  // 直接標示終點值最大的前 4 位（其餘看圖例），避免文字重疊
  const finals = series.map((s, i) => ({i, y: s.pts[s.pts.length - 1].y}))
    .sort((a, b) => Math.abs(b.y) - Math.abs(a.y)).slice(0, 4)
    .map(f => ({...f, py: Math.max(T + 6, Math.min(H - B - 4, Y(f.y)))}))
    .sort((a, b) => a.py - b.py);
  for (let k = 1; k < finals.length; k++)
    if (finals[k].py - finals[k-1].py < 13) finals[k].py = finals[k-1].py + 13;
  finals.forEach(f => {
    const s = series[f.i];
    g += `<text x="${W-R+8}" y="${(f.py+4).toFixed(1)}" font-size="11.5" fill="var(--ink2)">${esc(s.name)} <tspan fill="var(--ink)" font-weight="650">${pct(f.y)}</tspan></text>`;
  });
  g += `<line id="curveCross" x1="0" x2="0" y1="${T}" y2="${H-B}" stroke="var(--baseline)" stroke-width="1" style="display:none"/>`;
  g += `<rect id="curveOverlay" x="${L}" y="${T}" width="${W-L-R}" height="${H-T-B}" fill="transparent"/>`;

  const legend = series.map(s =>
    `<span class="it"><span class="sw" style="background:${s.color}"></span>${esc(s.name)}（${s.pts.length}筆）</span>`).join("");
  const seg = `<div class="seg">` + HORIZONS.map(h =>
    `<button class="${h===curveH?'on':''}" onclick="setCurveH('${h}')">${HL[h]}</button>`).join("") + `</div>`;
  return `<div class="card">
    <div class="chart-head"><span class="chart-title">累積超額報酬（等權跟單）</span>${seg}</div>
    <div class="legend">${legend}</div>
    <div class="viz"><svg id="curveSvg" viewBox="0 0 ${W} ${H}" role="img" aria-label="老師累積超額報酬曲線">${g}</svg><div class="tip" id="curveTip"></div></div>
    <div class="note" style="margin-bottom:0">每筆有明確多空方向且有股價資料的推薦等權計算，依進場日累加「多空調整後超額報酬」（看空反向計）；僅顯示樣本最多的前 ${series.length} 位老師。</div>
  </div>`;
}

function wireCurveHover() {
  const svg = document.getElementById("curveSvg");
  if (!svg || !window._curve) return;
  const st = window._curve;
  const Xv = v => st.L + (v - st.x0) / (st.x1 - st.x0 || 1) * (st.W - st.L - st.R);
  const overlay = document.getElementById("curveOverlay");
  const cross = document.getElementById("curveCross");
  const tip = document.getElementById("curveTip");
  const unionX = [...new Set(st.series.flatMap(s => s.pts.map(p => p.x)))].sort((a, b) => a - b);
  overlay.addEventListener("mousemove", ev => {
    const r = svg.getBoundingClientRect();
    const mx = (ev.clientX - r.left) * st.W / r.width;
    const t = st.x0 + (mx - st.L) / (st.W - st.L - st.R) * (st.x1 - st.x0);
    let best = unionX[0];
    unionX.forEach(u => { if (Math.abs(u - t) < Math.abs(best - t)) best = u; });
    const px = Xv(best).toFixed(1);
    cross.setAttribute("x1", px); cross.setAttribute("x2", px);
    cross.style.display = "";
    let rows = "";
    st.series.forEach(s => {
      let last = null;
      for (const p of s.pts) { if (p.x <= best) last = p; else break; }
      if (last) rows += `<div class="row"><span class="sw" style="background:${s.color};width:10px;height:3px;border-radius:2px;display:inline-block"></span>${esc(s.name)}<span class="num">${pct(last.y)}</span></div>`;
    });
    tip.innerHTML = `<div style="color:var(--muted);margin-bottom:2px">${new Date(best).toISOString().slice(0,10)} 為止</div>` + rows;
    tip.style.display = "block";
    const cont = svg.parentElement, cr = cont.getBoundingClientRect();
    let tx = ev.clientX - cr.left + 14, ty = ev.clientY - cr.top + 12;
    if (tx + tip.offsetWidth > cr.width) tx = tx - tip.offsetWidth - 26;
    tip.style.left = tx + "px"; tip.style.top = ty + "px";
  });
  overlay.addEventListener("mouseleave", () => {
    cross.style.display = "none"; tip.style.display = "none";
  });
}

function teacherTable() {
  const rows = DATA.teacherStats;
  if (!rows.length) return `<div class="note">尚無資料。</div>`;
  const maxAbs = Math.max(0.02, ...rows.map(r => Math.abs(r.h.m1.avg_excess || 0)));
  let html = `<div class="note">只有「看多/看空」的推薦計分（中性/觀望不計）。命中率＝依多空方向調整後報酬 &gt; 0 的比例，下方小字為 Wilson 95% 信賴區間；<b>粗體</b>＝區間下緣高於 50%，統計上優於擲硬幣。平均超額＝相對加權指數同期。</div>`;
  html += `<div class="card" style="overflow-x:auto"><table><thead><tr>
    <th>老師</th><th class="num">推薦/計分</th><th class="num">集數</th>`
    + HORIZONS.map(h => `<th class="num">${HL[h]}命中率</th>`).join("")
    + `<th class="num">+1月平均報酬</th><th>+1月平均超額（vs 大盤）</th></tr></thead><tbody>`;
  rows.forEach(r => {
    html += `<tr><td><span class="teacher-name">${esc(r.name)}</span></td>
      <td class="num">${r.n_picks} / ${r.n_scored}</td><td class="num">${r.n_episodes}</td>`
      + HORIZONS.map(h => {
          const s = r.h[h], hr = s.hit_rate, ci = s.hit_ci;
          if (hr === null) return `<td class="num">–</td>`;
          const sig = ci && ci[0] > 0.5;
          const ciTxt = ci ? `${(ci[0]*100).toFixed(0)}–${(ci[1]*100).toFixed(0)}%` : "";
          return `<td class="num"><span class="${sig ? 'sig' : ''}">${(hr*100).toFixed(0)}%</span> <span style="color:var(--muted);font-size:11px">(${s.n})</span><div class="ci">${ciTxt}</div></td>`;
        }).join("")
      + `<td class="num ${cls(r.h.m1.avg_ret)}">${pct(r.h.m1.avg_ret)}</td>
      <td>${bar(r.h.m1.avg_excess, maxAbs)}</td></tr>`;
  });
  return html + `</tbody></table></div>`;
}

// ---- 資料品質面板 ----
let qFilter = "all";
const QL = {no_ticker: "代號未對應", low_conf: "歸屬信心低", no_price: "無股價資料"};
function setQFilter(f) { qFilter = f; render(); }

function qualityView() {
  const q = DATA.quality || {items: [], counts: {}};
  const epUrl = {};
  DATA.episodes.forEach(e => { epUrl[e.id] = e.url; });
  let html = `<div class="note">人工抽查 LLM 抽取品質用：代號對不上證交所對照表、老師歸屬信心低（逐字稿無講者標記，靠引導語推斷）、或有代號但抓不到股價的推薦。點日期可開啟該集影片，對照「原句」驗證歸屬是否正確。</div>`;
  html += `<div class="tiles">` + ["no_ticker","low_conf","no_price"].map(k =>
    `<div class="tile"><div class="v">${q.counts[k] || 0}</div><div class="k">${QL[k]}</div></div>`).join("") + `</div>`;
  html += `<div class="chips">` + ["all","no_ticker","low_conf","no_price"].map(f =>
    `<button class="chipbtn ${qFilter===f?'on':''}" onclick="setQFilter('${f}')">${f==="all"?"全部":QL[f]}</button>`).join("") + `</div>`;
  const items = q.items.filter(it => qFilter === "all" || it.issues.includes(qFilter));
  html += `<div class="card" style="overflow-x:auto"><table><thead><tr>
    <th>日期</th><th>老師</th><th>股票</th><th>多空</th><th>問題</th><th>信心</th><th>原句（逐字稿）</th></tr></thead><tbody>`;
  items.forEach(it => {
    html += `<tr>
      <td style="white-space:nowrap"><a href="${epUrl[it.video_id] || '#'}" target="_blank">${it.date}</a></td>
      <td>${esc(it.teacher)}</td>
      <td style="white-space:nowrap">${esc(it.stock_name)} ${it.ticker ? `<span style="color:var(--muted)">(${esc(it.ticker)})</span>` : ""}</td>
      <td>${stanceChip(it.stance)}</td>
      <td>${it.issues.map(x => `<span class="issue">${QL[x]}</span>`).join("")}</td>
      <td>${esc(it.confidence || "–")}</td>
      <td class="qquote">「${esc(it.quote)}」</td></tr>`;
  });
  html += `</tbody></table></div>`;
  if (!items.length) html += `<div class="note">沒有符合的項目。</div>`;
  return html;
}

function stockTable() {
  const rows = DATA.stockStats;
  if (!rows.length) return `<div class="note">尚無資料。</div>`;
  let html = `<input class="search" placeholder="搜尋股票名稱或代號..." oninput="filterStocks(this.value)">`;
  html += `<div class="card" style="overflow-x:auto"><table><thead><tr>
    <th>股票</th><th class="num">被提及</th><th class="num">看多</th><th class="num">看空</th>
    <th>推薦老師</th><th class="num">+1月平均報酬</th><th class="num">+1月平均超額</th><th>最近提及</th>
    </tr></thead><tbody id="stockRows">`;
  html += rows.map(stockRow).join("");
  return html + `</tbody></table></div>`;
}
function stockRow(r) {
  return `<tr data-key="${esc(r.stock_name)} ${r.ticker||""}">
    <td>${esc(r.stock_name)} ${r.ticker ? `<span style="color:var(--muted)">(${r.ticker})</span>` : ""}</td>
    <td class="num">${r.n}</td><td class="num">${r.bull}</td><td class="num">${r.bear}</td>
    <td>${r.teachers.map(esc).join("、")}</td>
    <td class="num ${cls(r.avg_ret_m1)}">${pct(r.avg_ret_m1)}</td>
    <td class="num ${cls(r.avg_ex_m1)}">${pct(r.avg_ex_m1)}</td>
    <td>${r.last_date}</td></tr>`;
}
function filterStocks(q) {
  q = q.trim().toLowerCase();
  document.querySelectorAll("#stockRows tr").forEach(tr => {
    tr.style.display = !q || tr.dataset.key.toLowerCase().includes(q) ? "" : "none";
  });
}

const initView = location.hash.replace("#", "");
if (["episodes", "teachers", "stocks", "quality"].includes(initView)) {
  view = initView;
  document.querySelectorAll(".tab").forEach(t =>
    t.classList.toggle("active", t.dataset.view === view));
}
renderTiles();
render();
</script>
</body>
</html>
"""


def main():
    episodes = load_data()
    data = {
        "episodes": episodes,
        "teacherStats": compute_teacher_stats(episodes),
        "stockStats": compute_stock_stats(episodes),
        "teacherCurves": compute_teacher_curves(episodes),
        "quality": compute_quality(episodes),
    }
    html = (HTML_TEMPLATE
            .replace("__GENERATED__", datetime.now().strftime("%Y-%m-%d %H:%M"))
            .replace("__DATA__", json.dumps(data, ensure_ascii=False)))
    DASHBOARD_HTML.parent.mkdir(parents=True, exist_ok=True)
    DASHBOARD_HTML.write_text(html, encoding="utf-8")
    print(f"Dashboard: {DASHBOARD_HTML} ({len(episodes)} episodes)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()

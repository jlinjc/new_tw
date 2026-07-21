# -*- coding: utf-8 -*-
import json
import sys

from analyze import normalize_tickers
from config import ANALYSIS_DIR, TICKERS_JSON

sys.stdout.reconfigure(encoding="utf-8")
tickers = json.loads(TICKERS_JSON.read_text(encoding="utf-8"))
f = ANALYSIS_DIR / "iOIFQ7_CTF4.json"
data = json.loads(f.read_text(encoding="utf-8"))
before = {(t["name"], p["stock_name"]): p["ticker"]
          for t in data["teachers"] for p in t["picks"]}
data = normalize_tickers(data, tickers)
for t in data["teachers"]:
    for p in t["picks"]:
        old = before[(t["name"], p["stock_name"])]
        mark = "" if old == p["ticker"] else f"  <-- corrected from {old}"
        print(f"{t['name']}: {p['stock_name']} {p['ticker']} ({p.get('tw_market')}){mark}")
f.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

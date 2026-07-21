# -*- coding: utf-8 -*-
import json
import sys

from config import PERFORMANCE_JSON

sys.stdout.reconfigure(encoding="utf-8")
rows = json.loads(PERFORMANCE_JSON.read_text(encoding="utf-8"))
for r in rows:
    p = r["perf"]
    if not p:
        continue
    d3 = p["ret"]["d3"]
    print(f"{r['teacher']:4s} {r['stock_name']:6s}({r['ticker']}) {r['stance']} "
          f"進場{p['entry_date']}@{p['entry_price']} "
          f"+3日:{'' if d3 is None else format(d3*100, '+.1f')+'%'} "
          f"hit:{r['hit'].get('d3')}")

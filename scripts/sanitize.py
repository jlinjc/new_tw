# -*- coding: utf-8 -*-
"""校正 LLM 分析輸出的欄位偏差（CLI 模式沒有 json_schema 強制力）。

可 import（analyze_free 對新輸出即時校正），也可直接執行校正
data/analysis/ 下所有既有檔案：python sanitize.py
"""
import json
import sys

from config import ANALYSIS_DIR

STANCES = {"看多", "看空", "中性", "觀望"}
MARKETS = {"TW", "US", "ETF", "OTHER"}
PICK_DEFAULTS = {
    "ticker": None, "action": None, "reasons": [], "target_price": None,
    "confidence": "low", "quote": "",
}


def sanitize_pick(p: dict) -> dict:
    # 模型偶爾把 stance 值寫進 market 欄位
    if p.get("market") in STANCES and "stance" not in p:
        p["stance"] = p["market"]
        p["market"] = None
    if p.get("stance") not in STANCES:
        p["stance"] = "中性"
    if p.get("market") not in MARKETS:
        tk = p.get("ticker") or ""
        p["market"] = "TW" if (isinstance(tk, str) and tk.isdigit()) else "OTHER"
    for key, default in PICK_DEFAULTS.items():
        p.setdefault(key, default)
    if not isinstance(p.get("reasons"), list):
        p["reasons"] = [str(p["reasons"])]
    p.setdefault("stock_name", "")
    return p


def sanitize_result(result: dict) -> dict:
    result.setdefault("episode_summary", "")
    result.setdefault("market_view", "")
    result.setdefault("teachers", [])
    for t in result["teachers"]:
        t.setdefault("name", "（未知）")
        t.setdefault("picks", [])
        t["picks"] = [sanitize_pick(p) for p in t["picks"]]
    return result


def main():
    fixed = 0
    for f in sorted(ANALYSIS_DIR.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        before = json.dumps(data, ensure_ascii=False, sort_keys=True)
        data = sanitize_result(data)
        if json.dumps(data, ensure_ascii=False, sort_keys=True) != before:
            f.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                         encoding="utf-8")
            fixed += 1
            print(f"fixed: {f.name}")
    print(f"Sanitized, files changed: {fixed}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()

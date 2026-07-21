# -*- coding: utf-8 -*-
"""校正 LLM 分析輸出的欄位偏差（CLI 模式沒有 json_schema 強制力）。

可 import（analyze_free 對新輸出即時校正），也可直接執行校正
data/analysis/ 下所有既有檔案：python sanitize.py
"""
import json
import re
import sys

from config import ANALYSIS_DIR, TICKERS_JSON

STANCES = {"看多", "看空", "中性", "觀望"}
MARKETS = {"TW", "US", "ETF", "OTHER"}
PICK_DEFAULTS = {
    "ticker": None, "action": None, "reasons": [], "target_price": None,
    "confidence": "low", "quote": "",
}

# 逐字稿語音辨識同音字誤植，會把同一位老師拆成兩人、稀釋樣本數與信賴區間。
# 正確拼法以集數標題（主持人固定引導語列出的老師名單）為準，逐一核對過。
TEACHER_ALIASES = {
    "翁世峻": "翁士峻",
    "高憲榮": "高憲容",
}


def canonical_teacher_name(name: str) -> str:
    name = re.sub(r"[（(][^）)]*[）)]\s*$", "", name).strip()  # 去掉括號暱稱後綴
    return TEACHER_ALIASES.get(name, name)


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
    # 模型有時把辨識校正註記寫進 stock_name 本身（如「日電貿（語音辨識原文為…）」），
    # 導致證交所對照表的名稱比對失敗、代號校正被跳過。註記移到獨立欄位保留可讀性。
    m = re.match(r"^(.+?)[（(]([^）)]*)[）)]\s*$", p["stock_name"])
    if m:
        p["stock_name"] = m.group(1).strip()
        p.setdefault("name_note", m.group(2).strip())
    return p


def sanitize_result(result: dict) -> dict:
    result.setdefault("episode_summary", "")
    result.setdefault("market_view", "")
    result.setdefault("teachers", [])
    for t in result["teachers"]:
        t.setdefault("name", "（未知）")
        t["name"] = canonical_teacher_name(t["name"])
        t.setdefault("picks", [])
        t["picks"] = [sanitize_pick(p) for p in t["picks"]]
    # 同一集內，正規化後可能有兩筆同名老師（例如原本一個帶暱稱、一個不帶）；合併
    merged = {}
    for t in result["teachers"]:
        if t["name"] in merged:
            merged[t["name"]]["picks"].extend(t["picks"])
        else:
            merged[t["name"]] = t
    result["teachers"] = list(merged.values())
    return result


def main():
    from analyze import normalize_tickers  # 延後 import，避免無謂載入 anthropic 套件
    tickers = json.loads(TICKERS_JSON.read_text(encoding="utf-8"))
    fixed = 0
    for f in sorted(ANALYSIS_DIR.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        before = json.dumps(data, ensure_ascii=False, sort_keys=True)
        data = sanitize_result(data)
        data = normalize_tickers(data, tickers)  # stock_name 清乾淨後代號才能重新對照
        if json.dumps(data, ensure_ascii=False, sort_keys=True) != before:
            f.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                         encoding="utf-8")
            fixed += 1
            print(f"fixed: {f.name}")
    print(f"Sanitized, files changed: {fixed}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()

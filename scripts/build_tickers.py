# -*- coding: utf-8 -*-
"""從證交所 ISIN 網站抓上市/上櫃股票清單，建立 名稱→代號 對照表。

輸出 data/tw_tickers.json：{"台積電": {"code": "2330", "market": "TWSE"}, ...}
"""
import json
import re
import sys

import requests

from config import TICKERS_JSON

SOURCES = [
    ("https://isin.twse.com.tw/isin/C_public.jsp?strMode=2", "TWSE"),  # 上市
    ("https://isin.twse.com.tw/isin/C_public.jsp?strMode=4", "TPEX"),  # 上櫃
]
ROW_RE = re.compile(r"<td[^>]*>([0-9A-Z]{4,6})[　\s]+([^<]+?)</td>")


def main():
    tickers = {}
    for url, market in SOURCES:
        r = requests.get(url, timeout=60,
                         headers={"User-Agent": "Mozilla/5.0"})
        r.encoding = "ms950"
        count = 0
        for m in ROW_RE.finditer(r.text):
            code, name = m.group(1).strip(), m.group(2).strip()
            # 只留 4 碼純數字（一般股票），排除權證/ETF以外先全收，ETF 也可能被推薦
            if not re.fullmatch(r"\d{4,6}", code):
                continue
            if name not in tickers:  # 上市優先
                tickers[name] = {"code": code, "market": market}
                count += 1
        print(f"{market}: {count} entries")
    TICKERS_JSON.write_text(json.dumps(tickers, ensure_ascii=False, indent=1),
                            encoding="utf-8")
    print(f"Total {len(tickers)} tickers -> {TICKERS_JSON}")
    for probe in ("台積電", "鴻海", "南亞科", "台燿"):
        print(" ", probe, tickers.get(probe))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()

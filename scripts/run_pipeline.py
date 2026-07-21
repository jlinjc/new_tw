# -*- coding: utf-8 -*-
"""一鍵執行完整 pipeline（各步驟皆可中斷續跑、已完成者自動跳過）。

需要環境變數：GROQ_API_KEY（語音轉錄）。
內容分析走 Claude Code CLI（訂閱額度，analyze_free.py），不需要 ANTHROPIC_API_KEY。
用法：python run_pipeline.py
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STEPS = [
    ("抓取影片清單", "fetch_videos.py"),
    ("語音轉錄", "transcribe.py"),
    ("LLM 內容分析（訂閱額度）", "analyze_free.py"),
    ("校正分析欄位", "sanitize.py"),
    ("計算股價表現", "performance.py"),
    ("產生 dashboard", "build_dashboard.py"),
]


def main():
    for name, script in STEPS:
        print(f"\n===== {name} ({script}) =====", flush=True)
        r = subprocess.run([sys.executable, str(HERE / script)])
        if r.returncode != 0:
            print(f"步驟失敗：{name}，中止。修正後重跑即可續傳。")
            sys.exit(1)
    print("\n完成！開啟 output/dashboard.html 查看結果。")


if __name__ == "__main__":
    main()

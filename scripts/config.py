# -*- coding: utf-8 -*-
"""共用設定"""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SUBTITLE_DIR = DATA_DIR / "subtitles"
ANALYSIS_DIR = DATA_DIR / "analysis"
OUTPUT_DIR = PROJECT_ROOT / "output"

VIDEOS_JSON = DATA_DIR / "videos.json"
TICKERS_JSON = DATA_DIR / "tw_tickers.json"
PERFORMANCE_JSON = DATA_DIR / "performance.json"
DASHBOARD_HTML = OUTPUT_DIR / "dashboard.html"

CHANNEL_URL = "https://www.youtube.com/@EBCmoneyshow/videos"

# 回溯天數（約 3 個月）
LOOKBACK_DAYS = 92

# 完整版集數的標題篩選條件
TITLE_MUST_CONTAIN = ["理財達人秀"]
TITLE_ANY_OF = ["完整版"]

# LLM 分析設定
# 使用者在規劃時選擇了 Sonnet 方案以控制成本（3 個月約 65 集，估 3-5 美元）。
# 想要更高品質可改成 "claude-opus-4-8"（成本約兩倍）。
ANALYSIS_MODEL = "claude-sonnet-5"

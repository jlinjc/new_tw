# 理財達人秀 老師推薦追蹤

自動抓取東森《理財達人秀》每集內容，分析每位老師推薦的股票與理由，
並追蹤推薦後 +3日/+1週/+2週/+1月 的股價表現與相對大盤的超額報酬。

## 重要背景

EBC 頻道**停用了 YouTube 字幕**（畫面上的字幕是燒錄在影片裡的），
因此逐字稿由 Groq Whisper (whisper-large-v3-turbo) 從音訊辨識產生。

## 需要的環境變數

| 變數 | 用途 | 取得方式 |
|---|---|---|
| `GROQ_API_KEY` | 語音轉錄 | https://console.groq.com （有免費額度）|

設定（永久生效，設完要開新終端機）：

```powershell
setx GROQ_API_KEY "gsk_..."
```

內容分析走 **Claude Code CLI 訂閱額度**（`analyze_free.py`，headless `claude -p`），
不需要 ANTHROPIC_API_KEY、不另外付 API 費。若日後想改走 API，
`analyze.py` 仍保留（需 `ANTHROPIC_API_KEY`）。

## 執行

```powershell
cd C:\Users\Jason\ebc-money-show\scripts
python run_pipeline.py
```

每一步都可中斷續跑（已完成的集數自動跳過）。之後每天要更新，重跑同一指令即可，
只會處理新集數。完成後開啟 `output/dashboard.html`。

## 各步驟

| 腳本 | 功能 | 輸出 |
|---|---|---|
| `fetch_videos.py` | 抓最近 3 個月完整版集數清單 | `data/videos.json` |
| `transcribe.py` | 下載音訊 → Groq Whisper 逐字稿 | `data/subtitles/{id}.txt` |
| `build_tickers.py` | 證交所上市/上櫃 名稱→代號表 | `data/tw_tickers.json` |
| `analyze_free.py` | Claude Code CLI（訂閱額度）抽取老師/推薦/理由 | `data/analysis/{id}.json` |
| `sanitize.py` | 校正 LLM 輸出的欄位偏差 | 原地修正 analysis |
| `performance.py` | yfinance 計算後續報酬+超額 | `data/performance.json` |
| `build_dashboard.py` | 產生互動式儀表板 | `output/dashboard.html` |

單獨處理特定集數：`python transcribe.py <video_id>`、`python analyze_free.py <video_id>`

## 方法論定義

- **進場點**：播出日之後第一個交易日的收盤價（節目為盤後播出，隔天才能行動）
- **視窗**：進場後 +3 / +5 / +10 / +21 個「交易日」
- **命中**：依多空方向調整後報酬 > 0（看空標的下跌算命中）；
  **只有「看多/看空」的推薦計分**，中性/觀望僅展示不評分
- **信賴區間**：命中率附 Wilson 95% CI；區間下緣 > 50% 才算統計上優於亂猜
- **超額報酬**：個股報酬 − 加權指數(^TWII)同期報酬（^TWII 為不含息價格指數，
  個股有還原息值，超額會略偏高）
- **累積曲線**：老師排行頁的折線圖＝每筆計分推薦等權、依進場日累加多空調整後超額
- **資料品質**：dashboard「資料品質」分頁列出代號未對應/歸屬信心低/無股價的推薦，
  附逐字稿原句供人工抽查
- **模型**：`analyze_free.py` 用 Claude Code CLI 的 sonnet（訂閱額度）

## 已知限制

- 逐字稿無講者標記，老師歸屬靠主持人引導語推斷（不確定者標 confidence: low）
- 語音辨識對股票名稱有錯字，已用證交所對照表校正，但仍可能漏
- 美股標的目前僅在模型能給出正確代號時計算表現
- Groq 免費額度有音訊時數上限，64 集首次回填可能需分天跑或升級付費層

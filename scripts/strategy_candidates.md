# 策略搜尋提案（由對話歷史的既有教訓整理）

## 資料現況
- `data/analysis/*.json`：每集老師/推薦原始資料（stock_name, ticker, stance, confidence, mention_seq 等）
- `data/performance.json`：每筆推薦的績效（entry/exit、ret、excess，四個持有期 d3/w1/w2/m1，已對齊大盤同期區間，不需重算）
- 目前約 81 集分析、1978 筆推薦、1787 筆有績效，日期範圍 2026-05-07 ~ 2026-08-28

## 必須遵守的方法論規則（前面已踩過的坑，不可重犯）
1. **部位去重**：測「共識」或「全部推薦」類的 cohort 時，同一集、同一標的、同一方向若被多位老師推薦，必須先合併成 1 個部位再計算勝率/平均超額，不可每位老師各算一次（否則會像先前把 30 筆的共識灌水成虛胖數字，真實只有 15 個獨立事件）。老師個人排行才允許逐筆計算。
2. **成本**：只要是「可操作」策略，一定要附扣成本後的淨值。來回成本抓 `ROUNDTRIP_COST = 0.0057`（見 build_dashboard.py）。
3. **樣本門檻**：n < 15 的 cohort，數字再好看都要標記「樣本不足，不可信」，不能當結論。
4. **時間穩定性**：每個候選策略都要切成前半段（約 05-07~06-25）與後半段（約 06-26~08-28）分別算一次。真正穩定的策略應該兩段同號、量級相近；只有一段亮眼、另一段翻負的，是過擬合/運氣，要老實標記。
5. **正負號**：`excess` 欄位是原始（未依多空調整），使用時要乘 `sign = -1 if stance=='看空' else 1`。
6. **大盤對齊**：`performance.json` 的 `excess` 已經用個股實際進出場日對齊大盤計算過，直接用，不要重算。

## 要測試的候選策略（都用「部位」為單位，逐一輸出結果）

以 d3（3個交易日）為主要持有期（已知短線才有真實訊號），同時附 m1 做對照；除非特別註明。

1. `baseline_all` — 全部有明確多空方向且有績效的部位，無篩選（對照組）
2. `core5_d3` — 只跟短線核心老師（容逸燊/李永年/張林忠/鍾國忠/黃豐凱）
3. `core5_selective_d3` — core5 + 該老師當集點名 ≤5 檔（精選日）
4. `core5_highconf_d3` — core5 + 該筆信心 high
5. `core5_selective_highconf_d3` — core5 + 精選日 + 高信心（三條件疊加，樣本可能很小，照實回報）
6. `rongyisheng_only_d3` — 只跟容逸燊一人
7. `consensus2_d3` — 同集 ≥2 位老師同看同標的同方向（部位去重後）
8. `consensus2_m1` — 同上但 m1 持有（對照組，預期較差）
9. `consensus_core_d3` — 同集 ≥2 位老師同看，且其中至少 1 位是 core5 成員
10. `highconf_only_d3` / `highconf_only_m1` — 全部老師，只留信心 high
11. `avoid_excluded_d3` — 排除長期穩定跑輸的老師（朱家泓/紀緯明/高憲容/權證小哥/翁士峻/蔡明翰/林漢偉，且不含 core5）後的全部推薦
12. `bull_regime_only_m1` — 只留「該部位持有窗口內大盤原始報酬 > 0」的部位（用 ret - excess 還原大盤報酬判斷），m1 持有
13. `tsmc_only` — 只交易 2330 台積電（不分老師），d3 與 m1 都算；驗證先前發現的「~90% 勝率」在更大樣本下是否持續或已經回歸均值
14. `tw_stock_only_d3` — 排除 ETF 與美股，只留台股個股
15. `first_mention_only_d3` / `repeat_only_d3` — 首次點名 vs 重申（用 `mention_seq` 欄位），各自的 d3 表現
16. `weighted_score_top20pct` — 綜合評分（信心 high=2分/medium=1分、consensus 人數、是否 core5 成員 +2分、是否精選日 +1分）算出每筆分數，每週取分數前 20% 的部位，看 d3/m1 表現
17. `short_side_only` — 只看「看空」的部位（樣本小但先前發現命中率較高，需要專門驗證是否成立）
18. `tw_listed_only_d3` — 只交易上市(.TW)，排除上櫃(.TWO)小型股

## 輸出要求
- 為每個候選策略輸出：策略名稱、持有期、n（全期/前半/後半）、勝率+Wilson 95% CI（全期/前半/後半）、平均超額(毛)、平均超額(淨，扣成本)、單句判定（RELIABLE_CANDIDATE / TOO_SMALL_SAMPLE / INCONSISTENT_ACROSS_HALVES / NEGATIVE）
- 把分析腳本存成 `scripts/strategy_search.py`（可重複執行、之後資料變大可再跑）
- 完整結果存成 `data/strategy_search_results.json`，另外用 3-5 句話總結「哪些策略看起來最值得進一步驗證」

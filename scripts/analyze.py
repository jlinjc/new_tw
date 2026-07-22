# -*- coding: utf-8 -*-
"""用 Claude 分析每集逐字稿：抽取老師、推薦股票、理由、多空方向。

輸入 data/subtitles/{id}.txt，輸出 data/analysis/{id}.json。已存在者跳過。
用法：python analyze.py [video_id ...]
"""
import json
import os
import re
import sys

import anthropic

from config import (ANALYSIS_DIR, ANALYSIS_MODEL, SUBTITLE_DIR, TICKERS_JSON,
                    VIDEOS_JSON)

SCHEMA = {
    "type": "object",
    "properties": {
        "episode_summary": {"type": "string",
                            "description": "本集重點摘要（2-3 句，繁體中文）"},
        "market_view": {"type": "string",
                        "description": "本集對大盤的整體看法摘要（1-2 句）"},
        "teachers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "老師姓名（正規化，如：林漢偉）"},
                    "picks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "stock_name": {"type": "string",
                                               "description": "股票正式名稱（修正語音辨識錯字，如：台積電）"},
                                "ticker": {"type": ["string", "null"],
                                           "description": "台股4位數代號，如 2330；不確定則 null"},
                                "market": {"type": "string", "enum": ["TW", "US", "ETF", "OTHER"],
                                           "description": "TW=台股個股, US=美股, ETF=台股ETF, OTHER=指數/期貨/其他"},
                                "stance": {"type": "string",
                                           "enum": ["看多", "看空", "中性", "觀望"]},
                                "action": {"type": ["string", "null"],
                                           "description": "建議動作，如：逢低買進/拉回找買點/停損出場/續抱"},
                                "reasons": {"type": "array", "items": {"type": "string"},
                                            "description": "推薦理由，每點一句"},
                                "target_price": {"type": ["string", "null"],
                                                 "description": "有提到目標價/支撐壓力位則填"},
                                "confidence": {"type": "string", "enum": ["high", "medium", "low"],
                                               "description": "歸屬給這位老師的確定程度：high=明確由該老師推薦"},
                                "quote": {"type": "string",
                                          "description": "逐字稿中最能代表此推薦的原句（可略修錯字）"}
                            },
                            "required": ["stock_name", "ticker", "market", "stance",
                                         "action", "reasons", "target_price",
                                         "confidence", "quote"],
                            "additionalProperties": False
                        }
                    }
                },
                "required": ["name", "picks"],
                "additionalProperties": False
            }
        }
    },
    "required": ["episode_summary", "market_view", "teachers"],
    "additionalProperties": False
}

SYSTEM_PROMPT = """\
你是台股投資節目的內容分析師。使用者會給你東森《理財達人秀》某一集的語音辨識逐字稿。

任務：找出節目中每位「老師」（來賓分析師）明確表達看法的股票，包括：
- 推薦買進/看多的標的（含理由、進場條件、目標價）
- 看空/建議賣出/避開的標的
- 只有實質觀點的才算；主持人隨口念的股名、跑馬燈式的清單不算推薦。

注意事項：
1. 逐字稿來自語音辨識，股票名稱常有錯字（例如「台耀」可能是「台燿」、「南亞科」可能被辨識成「南雅科」）。請根據上下文修正成正式股票名稱，並給出正確的台股代號。不確定代號時填 null，不要瞎猜。
2. 逐字稿沒有講者標記。請靠主持人的引導語（「請○○老師」「○○老師怎麼看」）判斷段落屬於哪位老師。集數標題最後通常列出當集老師名單，可作參考。無法確定歸屬時 confidence 填 low。
3. 同一位老師對同一檔股票的看法合併成一筆。
4. 大盤（加權指數）的看法放 market_view，不要放進 picks；但如果老師明確推薦某檔 ETF（如 0050）則算 picks。
5. 全部用繁體中文。"""


def build_user_prompt(video: dict, transcript: str) -> str:
    return (f"集數標題：{video['title']}\n"
            f"播出日期：{video['date']}\n\n"
            f"以下是逐字稿：\n<transcript>\n{transcript}\n</transcript>")


_TW_SUFFIX = re.compile(
    r"(股份有限公司|控股|-?KY|\*|電子|科技|半導體|材料|光電|國際|工業|"
    r"實業|生技|建設|開發|投資|金control|金)$")


def _norm_name(s: str) -> str:
    """正規化股名以利比對：臺→台、去括號註記與常見後綴。"""
    s = (s or "").strip().replace("臺", "台")
    s = re.sub(r"[（(].*?[）)]", "", s).strip()
    prev = None
    while s and s != prev:  # 反覆去尾綴（如「力智電子」→「力智」）
        prev = s
        s = _TW_SUFFIX.sub("", s).strip()
    return s


def _build_lookup(tickers: dict):
    code2 = {}          # code -> (official_name, market)
    norm2 = {}          # normalized official name -> (code, market, official)
    for name, v in tickers.items():
        code2.setdefault(v["code"], (name, v["market"]))
        nn = _norm_name(name)
        if nn:  # 避免「材料-KY」等整段被當後綴 → 空字串誤配
            norm2.setdefault(nn, (v["code"], v["market"], name))
    return code2, norm2


def _same_company(spoken: str, official: str) -> bool:
    """股名與代號官方名是否可信為同一家：容忍簡稱與單字同音錯字。"""
    a, b = _norm_name(spoken), _norm_name(official)
    if not a or not b:
        return False
    short, long = sorted([a, b], key=len)
    if all(c in long for c in short):        # 簡稱：短名字元全在長名內
        return True
    common = len(set(a) & set(b))
    if common >= 2:                          # 共用 ≥2 字
        return True
    if len(a) == len(b) and common >= len(a) - 1:  # 同長度僅差 1 字（同音錯字）
        return True
    return False


def normalize_tickers(result: dict, tickers: dict) -> dict:
    """用證交所對照表驗證代號。以「股名」為權威來源：

    1) 股名（原文或正規化後）查得到 → 用表上的代號，覆蓋模型。
    2) 股名查不到、但模型給了代號：交叉檢查該代號的官方名是否與股名首二字
       相符；相符才採信，否則棄用代號（寧可無績效，也不算到錯的公司）。
    棄用的代號保留在 ticker_unverified 供人工查核。
    """
    code2, norm2 = _build_lookup(tickers)
    for teacher in result.get("teachers", []):
        for pick in teacher.get("picks", []):
            raw = pick.get("stock_name", "").strip()
            entry = tickers.get(raw)
            if not entry and _norm_name(raw):
                hit = norm2.get(_norm_name(raw))
                if hit:
                    entry = {"code": hit[0], "market": hit[1]}
            if entry:
                pick["ticker"] = entry["code"]
                pick["tw_market"] = entry["market"]
                pick.pop("ticker_unverified", None)
                continue
            # 股名無法對照
            code = pick.get("ticker")
            if code and code in code2:
                if _same_company(raw, code2[code][0]):
                    pick["tw_market"] = code2[code][1]  # 兜得上，採信
                    pick.pop("ticker_unverified", None)
                else:
                    # 代號指向不同公司 → 棄用，避免算到錯股票
                    pick["ticker_unverified"] = code
                    pick["ticker"] = None
                    pick["tw_market"] = None
            else:
                pick["tw_market"] = None
            # 代號確認無誤時，股名一律用證交所官方名（修 ASR 錯字如禾仲堂→禾伸堂）；
            # 原始辨識名保留在 spoken_name，引句仍是逐字稿原文。
            final = pick.get("ticker")
            if final and final in code2 and pick.get("stock_name") != code2[final][0]:
                pick.setdefault("spoken_name", pick.get("stock_name", ""))
                pick["stock_name"] = code2[final][0]
    return result


def analyze_one(client: anthropic.Anthropic, video: dict, tickers: dict) -> dict:
    transcript = (SUBTITLE_DIR / f"{video['id']}.txt").read_text(encoding="utf-8")
    with client.messages.stream(
        model=ANALYSIS_MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": build_user_prompt(video, transcript)}],
    ) as stream:
        message = stream.get_final_message()
    if message.stop_reason == "refusal":
        raise RuntimeError("model refused")
    text = next(b.text for b in message.content if b.type == "text")
    result = json.loads(text)
    result = normalize_tickers(result, tickers)
    result["video"] = video
    result["_usage"] = {"input": message.usage.input_tokens,
                        "output": message.usage.output_tokens}
    return result


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set")
        sys.exit(1)
    client = anthropic.Anthropic()
    tickers = json.loads(TICKERS_JSON.read_text(encoding="utf-8"))
    videos = json.loads(VIDEOS_JSON.read_text(encoding="utf-8"))
    only_ids = set(sys.argv[1:])
    if only_ids:
        videos = [v for v in videos if v["id"] in only_ids]
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    done = skipped = failed = 0
    for i, v in enumerate(videos, 1):
        out = ANALYSIS_DIR / f"{v['id']}.json"
        if out.exists():
            skipped += 1
            continue
        if not (SUBTITLE_DIR / f"{v['id']}.txt").exists():
            print(f"[{i}/{len(videos)}] {v['date']} no transcript, skip", flush=True)
            continue
        try:
            result = analyze_one(client, v, tickers)
            out.write_text(json.dumps(result, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            n_picks = sum(len(t["picks"]) for t in result["teachers"])
            print(f"[{i}/{len(videos)}] {v['date']} teachers={len(result['teachers'])} "
                  f"picks={n_picks} tokens={result['_usage']}", flush=True)
            done += 1
        except Exception as e:
            failed += 1
            print(f"[{i}/{len(videos)}] {v['date']} FAILED: {e}", flush=True)
    print(f"Analyzed: {done}, cached: {skipped}, failed: {failed}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()

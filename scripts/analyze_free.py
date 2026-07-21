# -*- coding: utf-8 -*-
"""用 Claude Code CLI（訂閱額度、免 API key）分析每集逐字稿。

與 analyze.py 相同的提示詞與 schema，但透過 `claude -p` headless 模式執行，
用量計入 Claude 訂閱，不需要 ANTHROPIC_API_KEY。

輸入 data/subtitles/{id}.txt，輸出 data/analysis/{id}.json。已存在者跳過。
用法：python analyze_free.py [video_id ...]
"""
import json
import re
import subprocess
import sys
import time

from analyze import SCHEMA, SYSTEM_PROMPT, build_user_prompt, normalize_tickers
from config import ANALYSIS_DIR, SUBTITLE_DIR, TICKERS_JSON, VIDEOS_JSON
from sanitize import sanitize_result

CLAUDE_EXE = (r"C:\Users\Jason\.vscode\extensions"
              r"\anthropic.claude-code-2.1.212-win32-x64"
              r"\resources\native-binary\claude.exe")
MODEL = "sonnet"
REQUIRED_KEYS = {"episode_summary", "market_view", "teachers"}
# claude.exe 有時把連線錯誤印到 stdout（非 stderr）且 returncode=1
TRANSIENT_MARKERS = ("ENOTFOUND", "ECONNRESET", "ETIMEDOUT", "Unable to connect")


class SessionLimitError(RuntimeError):
    """訂閱用量已達上限，重試無意義，整批應立即停止。"""


def build_prompt(video: dict, transcript: str) -> str:
    return (
        f"{SYSTEM_PROMPT}\n\n"
        f"{build_user_prompt(video, transcript)}\n\n"
        "輸出格式：只輸出一個符合下列 JSON Schema 的 JSON 物件，"
        "不要 markdown code fence、不要任何其他文字：\n"
        f"{json.dumps(SCHEMA, ensure_ascii=False)}"
    )


def extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start = text.find("{")
    if start < 0:
        raise ValueError(f"no JSON in output: {text[:200]!r}")
    return json.loads(text[start:text.rfind("}") + 1])


def analyze_one(video: dict, tickers: dict, retries: int = 4) -> dict:
    transcript = (SUBTITLE_DIR / f"{video['id']}.txt").read_text(encoding="utf-8")
    prompt = build_prompt(video, transcript)
    last_err = None
    for attempt in range(retries + 1):
        r = subprocess.run(
            [CLAUDE_EXE, "-p", "--model", MODEL],
            input=prompt, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=900)
        if r.returncode != 0:
            msg = (r.stderr.strip() or r.stdout.strip())[:300]
            if "session limit" in msg.lower():
                raise SessionLimitError(msg)
            last_err = RuntimeError(f"claude exit {r.returncode}: {msg}")
            if any(m in msg for m in TRANSIENT_MARKERS) and attempt < retries:
                wait = 10 * (attempt + 1)
                print(f"    transient error, retry in {wait}s: {msg}", flush=True)
                time.sleep(wait)
                continue
            continue
        try:
            result = extract_json(r.stdout)
            missing = REQUIRED_KEYS - result.keys()
            if missing:
                raise ValueError(f"missing keys: {missing}")
            result = sanitize_result(result)
            result = normalize_tickers(result, tickers)
            result["video"] = video
            result["_usage"] = {"mode": "claude-code-cli", "model": MODEL}
            return result
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
    raise last_err


def main():
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
            result = analyze_one(v, tickers)
            out.write_text(json.dumps(result, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            n_picks = sum(len(t["picks"]) for t in result["teachers"])
            print(f"[{i}/{len(videos)}] {v['date']} teachers={len(result['teachers'])} "
                  f"picks={n_picks}", flush=True)
            done += 1
        except SessionLimitError as e:
            print(f"[{i}/{len(videos)}] {v['date']} SESSION LIMIT, stopping: {e}",
                  flush=True)
            break
        except Exception as e:
            failed += 1
            print(f"[{i}/{len(videos)}] {v['date']} FAILED: {e}", flush=True)
    print(f"Analyzed: {done}, cached: {skipped}, failed: {failed}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()

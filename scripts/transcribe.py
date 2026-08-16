# -*- coding: utf-8 -*-
"""用 Groq Whisper 將每集音訊轉成逐字稿。

流程：yt-dlp 下載音訊 → ffmpeg 轉 16kHz 單聲道 mp3 → 切成 10 分鐘段
→ Groq whisper-large-v3-turbo 辨識 → 合併存 data/subtitles/{id}.txt

已存在逐字稿者跳過，可中斷續跑。撞到 rate limit 會自動等待重試。
用法：python transcribe.py [video_id ...]   （不帶參數 = 全部集數）
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yt_dlp
from groq import Groq, RateLimitError, APIStatusError

from config import SUBTITLE_DIR, VIDEOS_JSON

WHISPER_MODEL = "whisper-large-v3-turbo"
WHISPER_PROMPT = "台股投資節目《理財達人秀》逐字稿，繁體中文，內容包含台積電等股票名稱與代號。"
CHUNK_SECONDS = 600  # 10 分鐘一段

YTDLP_OPTS = {
    "format": "bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "remote_components": ["ejs:github"],
    # YouTube 對 android_vr（yt-dlp 預設優先客戶端之一）的串流網址開始回
    # 403；web_embedded 目前不需要 PO Token 也能正常解析與下載。若未來
    # YouTube 又變，可用 `python -m yt_dlp --extractor-args
    # "youtube:player_client=<client>" --simulate <url>` 逐一測試候選客戶端。
    "extractor_args": {"youtube": {"player_client": ["web_embedded"]}},
}


def download_audio(url: str, workdir: Path) -> Path:
    opts = dict(YTDLP_OPTS)
    opts["outtmpl"] = str(workdir / "audio.%(ext)s")
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    files = [p for p in workdir.iterdir() if p.stem == "audio"]
    if not files:
        raise RuntimeError("audio download failed")
    return files[0]


def to_chunks(audio: Path, workdir: Path) -> list[Path]:
    """轉 16kHz 單聲道 mp3 並切段。"""
    pattern = workdir / "chunk_%03d.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(audio), "-ac", "1", "-ar", "16000",
         "-b:a", "48k", "-f", "segment", "-segment_time", str(CHUNK_SECONDS),
         str(pattern)],
        check=True, capture_output=True)
    return sorted(workdir.glob("chunk_*.mp3"))


def transcribe_chunk(client: Groq, chunk: Path, max_retries: int = 8) -> str:
    for attempt in range(max_retries):
        try:
            with open(chunk, "rb") as f:
                result = client.audio.transcriptions.create(
                    file=(chunk.name, f),
                    model=WHISPER_MODEL,
                    language="zh",
                    prompt=WHISPER_PROMPT,
                    response_format="text",
                )
            return result if isinstance(result, str) else result.text
        except RateLimitError as e:
            wait = 60
            try:
                wait = int(float(e.response.headers.get("retry-after", 60))) + 5
            except Exception:
                pass
            wait = min(wait, 3600)
            print(f"    rate limited, waiting {wait}s ...", flush=True)
            time.sleep(wait)
        except APIStatusError as e:
            if e.status_code >= 500:
                print(f"    server error {e.status_code}, retrying ...", flush=True)
                time.sleep(30)
            else:
                raise
    raise RuntimeError(f"chunk {chunk.name} failed after {max_retries} retries")


def transcribe_video(client: Groq, video: dict) -> bool:
    out_txt = SUBTITLE_DIR / f"{video['id']}.txt"
    if out_txt.exists():
        return True
    print(f"  downloading audio ...", flush=True)
    with tempfile.TemporaryDirectory() as td:
        workdir = Path(td)
        audio = download_audio(video["url"], workdir)
        chunks = to_chunks(audio, workdir)
        print(f"  {len(chunks)} chunks", flush=True)
        parts = []
        for i, chunk in enumerate(chunks, 1):
            text = transcribe_chunk(client, chunk)
            parts.append(text.strip())
            print(f"    chunk {i}/{len(chunks)} done", flush=True)
    out_txt.parent.mkdir(parents=True, exist_ok=True)
    out_txt.write_text("\n".join(parts), encoding="utf-8")
    return True


def main():
    if not os.environ.get("GROQ_API_KEY"):
        print("ERROR: GROQ_API_KEY not set")
        sys.exit(1)
    client = Groq()
    videos = json.loads(VIDEOS_JSON.read_text(encoding="utf-8"))
    only_ids = set(sys.argv[1:])
    if only_ids:
        videos = [v for v in videos if v["id"] in only_ids]
    done = failed = 0
    for i, v in enumerate(videos, 1):
        cached = (SUBTITLE_DIR / f"{v['id']}.txt").exists()
        print(f"[{i}/{len(videos)}] {v['date']} {v['id']}"
              f"{' (cached)' if cached else ''}", flush=True)
        try:
            transcribe_video(client, v)
            done += 1
        except Exception as e:
            failed += 1
            print(f"  FAILED: {e}", flush=True)
    print(f"Transcripts ready: {done}, failed: {failed}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()

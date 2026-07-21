# -*- coding: utf-8 -*-
"""抓取 EBC 理財達人秀頻道的完整版集數清單（最近 LOOKBACK_DAYS 天）。

輸出 data/videos.json：[{id, title, date, duration, url}, ...]（新到舊）
"""
import json
import re
import sys
from datetime import date, timedelta

import yt_dlp

from config import (CHANNEL_URL, LOOKBACK_DAYS, TITLE_ANY_OF,
                    TITLE_MUST_CONTAIN, VIDEOS_JSON)

DATE_RE = re.compile(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})")


def parse_title_date(title: str):
    m = DATE_RE.search(title)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def is_full_episode(title: str) -> bool:
    return (all(k in title for k in TITLE_MUST_CONTAIN)
            and any(k in title for k in TITLE_ANY_OF))


def main():
    cutoff = date.today() - timedelta(days=LOOKBACK_DAYS)
    ydl_opts = {
        "extract_flat": True,
        "quiet": True,
        "playlistend": 1500,  # 每天約 10 支影片，1500 足以涵蓋 3 個月
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(CHANNEL_URL, download=False)

    episodes = []
    stop_scanning = False
    for entry in info.get("entries", []):
        title = entry.get("title") or ""
        ep_date = parse_title_date(title)
        # 頻道大致按時間排序；連續出現太舊的影片就停止
        if ep_date and ep_date < cutoff - timedelta(days=14):
            stop_scanning = True
        if not is_full_episode(title):
            continue
        if ep_date is None or ep_date < cutoff:
            if stop_scanning:
                break
            continue
        episodes.append({
            "id": entry["id"],
            "title": title,
            "date": ep_date.isoformat(),
            "duration": entry.get("duration"),
            "url": f"https://www.youtube.com/watch?v={entry['id']}",
        })
        if stop_scanning:
            break

    episodes.sort(key=lambda e: e["date"], reverse=True)
    VIDEOS_JSON.parent.mkdir(parents=True, exist_ok=True)
    VIDEOS_JSON.write_text(json.dumps(episodes, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(f"Found {len(episodes)} full episodes since {cutoff.isoformat()}")
    for e in episodes[:5]:
        print(f"  {e['date']}  {e['title'][:60]}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()

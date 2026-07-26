# -*- coding: utf-8 -*-
"""每日全自動更新：抓新集 → 轉錄 → 分析 → 計算 → 建站 → 推上 GitHub。

供 Windows工作排程器 排程使用（見 scripts/register_daily_task.ps1）。
所有輸出寫入 data/daily_update.log，方便排程無人值守時事後排查。
任何一步失敗就停止並記錄，不會用半套資料覆蓋 dashboard；已完成的步驟
下次執行會自動跳過（各腳本本身已支援續跑）。
"""
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LOG = ROOT / "data" / "daily_update.log"

STEPS = [
    ("抓取影片清單", "fetch_videos.py"),
    ("語音轉錄", "transcribe.py"),
    ("LLM 內容分析（訂閱額度）", "analyze_free.py"),
    ("校正分析欄位", "sanitize.py"),
    ("計算股價表現", "performance.py"),
    ("追價衰減回測", "entry_delay.py"),
    ("產生 dashboard", "build_dashboard.py"),
]

# transcribe.py 需要呼叫 ffmpeg；排程器啟動的環境 PATH 可能不含 winget 安裝路徑，
# 這裡補上常見安裝位置，避免排程執行時因找不到 ffmpeg 而整批失敗。
FFMPEG_HINT_DIRS = [
    r"C:\Users\Jason\AppData\Local\Microsoft\WinGet\Packages"
    r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.2-full_build\bin",
]


def log(msg: str):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(cmd, cwd=None, env=None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd or ROOT, env=env,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")


def git(*args):
    r = run(["git", *args])
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def main():
    import os
    env = os.environ.copy()
    path_extra = [d for d in FFMPEG_HINT_DIRS if Path(d).exists()]
    if path_extra:
        env["PATH"] = ";".join(path_extra) + ";" + env.get("PATH", "")
    env.setdefault("PYTHONIOENCODING", "utf-8")

    log("===== 每日更新開始 =====")
    for name, script in STEPS:
        log(f"步驟：{name} ({script})")
        r = subprocess.run([sys.executable, str(HERE / script)],
                            cwd=HERE, env=env, capture_output=True,
                            text=True, encoding="utf-8", errors="replace")
        if r.stdout:
            log(r.stdout.strip()[-2000:])   # 只保留尾段，避免log過大
        if r.returncode != 0:
            log(f"❌ 步驟失敗：{name}\n{(r.stderr or '')[-2000:]}")
            log("===== 中止，未推送 =====")
            sys.exit(1)

    # 只有 dashboard/data 有變動才 commit，避免空提交
    rc, out, _ = git("status", "--porcelain")
    if not out.strip():
        log("無變動，略過 git commit/push")
        log("===== 每日更新完成（無變動）=====")
        return

    git("add", "-A")
    msg = f"每日自動更新 {datetime.now().strftime('%Y-%m-%d')}"
    rc, out, err = git("commit", "-m", msg)
    if rc != 0:
        log(f"❌ git commit 失敗：{err}")
        sys.exit(1)
    rc, out, err = git("push", "origin", "main")
    if rc != 0:
        log(f"❌ git push 失敗（本地已 commit，需手動 push）：{err}")
        sys.exit(1)
    log(f"✅ 已推送：{msg}")
    log("===== 每日更新完成 =====")


if __name__ == "__main__":
    main()

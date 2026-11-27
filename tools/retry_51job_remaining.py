"""冷却后逐词补采 51job 剩余关键词：每词独立进程，间隔 90 秒。"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
KEYWORDS = ["数据分析师", "测试工程师", "运维工程师", "嵌入式工程师"]
WAIT = 90  # 词间冷却
START_WAIT = 180  # 启动前先等接口限流解除


def main() -> None:
    print(f"先等待 {START_WAIT}s 让 51job 接口限流解除...", flush=True)
    time.sleep(START_WAIT)
    for i, kw in enumerate(KEYWORDS, 1):
        print(f"\n===== [{i}/{len(KEYWORDS)}] {kw} =====", flush=True)
        cmd = [str(PY), "-m", "collector", "--source", "51job",
               "--keywords", kw, "--delay", "5"]
        try:
            proc = subprocess.run(
                cmd, cwd=str(ROOT), timeout=240,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            out = proc.stdout or ""
            print(out[-1000:], flush=True)
            print(f"结果: rc={proc.returncode}", flush=True)
        except subprocess.TimeoutExpired as exc:
            raw = exc.stdout or ""
            out = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
            print("超时", flush=True)
            print(out[-600:], flush=True)
        if i < len(KEYWORDS):
            print(f"冷却 {WAIT}s...", flush=True)
            time.sleep(WAIT)
    print("\n补采任务结束", flush=True)


if __name__ == "__main__":
    main()

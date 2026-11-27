"""逐关键词起独立进程采集 51job，隔离 WAF 状态。

每个关键词用全新浏览器进程，单进程内只跑一个词；超时则杀掉进程继续下一个。
用法：python tools/collect_51job_batch.py 词1 词2 ...
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"
KEYWORDS = sys.argv[1:] or [
    "软件测试工程师", "人工智能", "后端开发", "前端开发", "Java", "Python",
    "算法工程师", "数据分析师", "测试工程师", "运维工程师", "嵌入式工程师",
]
TIMEOUT = 300  # 单个词最多 5 分钟


def main() -> None:
    results = []
    for i, kw in enumerate(KEYWORDS, 1):
        print(f"\n===== [{i}/{len(KEYWORDS)}] {kw} =====", flush=True)
        cmd = [
            str(PY), "-m", "collector", "--source", "51job",
            "--keywords", kw, "--delay", "3",
        ]
        start = time.time()
        try:
            proc = subprocess.run(
                cmd, cwd=str(ROOT), timeout=TIMEOUT,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            out = proc.stdout or ""
            print(out[-800:], flush=True)
            status = "OK" if proc.returncode == 0 else f"FAIL({proc.returncode})"
        except subprocess.TimeoutExpired as exc:
            out = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            print(f"超时（{TIMEOUT}s），已终止", flush=True)
            print(out[-400:], flush=True)
            status = "TIMEOUT"
        elapsed = time.time() - start
        results.append((kw, status, round(elapsed, 1)))
        if status == "OK":
            # 词与词之间休息，进一步降低 WAF 风险
            time.sleep(10)

    print("\n===== 汇总 =====", flush=True)
    for kw, status, elapsed in results:
        print(f"  {kw}: {status} ({elapsed}s)", flush=True)


if __name__ == "__main__":
    main()

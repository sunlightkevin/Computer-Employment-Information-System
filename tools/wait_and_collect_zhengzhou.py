"""等待 WAF 冷却后采集郑州（用户确认「现在就慢速重试」）。

每 CHECK_INTERVAL 秒用新会话探测一次详情页；一旦恢复，
调用 collector 主流程慢速采完郑州 150 条。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import subprocess
import time
from datetime import datetime

from collector.base import BrowserSession, is_waf_page

BASE = "https://www.yingjiesheng.com"
PROBE = f"{BASE}/job-008-150-613.html"
CHECK_INTERVAL = 90  # 每 90 秒探测一次
MAX_WAIT = 4 * 3600  # 最多等 4 小时


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def probe() -> bool:
    try:
        with BrowserSession() as session:
            result = session.get(PROBE)
            ok = not is_waf_page(result.html) and len(result.html) > 20000
            log(f"探测详情 page: len={len(result.html)} waf={is_waf_page(result.html)} -> {'恢复' if ok else '仍拦截'}")
            return ok
    except Exception as exc:
        log(f"探测异常: {type(exc).__name__} {exc}")
        return False


def main() -> int:
    waited = 0
    while waited < MAX_WAIT:
        if probe():
            log("详情页已恢复，开始慢速采集郑州（delay=5, session-size=10）")
            cmd = [
                sys.executable, "-u", "-m", "collector",
                "--source", "yingjiesheng",
                "--zhuanye-city", "zhengzhou",
                "--delay", "5.0",
                "--session-size", "10",
                "-v",
            ]
            result = subprocess.run(cmd, cwd=".", capture_output=False)
            log(f"采集结束，退出码 {result.returncode}")
            return result.returncode
        waited += CHECK_INTERVAL
        if waited < MAX_WAIT:
            log(f"等待 {CHECK_INTERVAL} 秒后重试（已等 {waited // 60} 分钟）")
            time.sleep(CHECK_INTERVAL)
    log("超过 4 小时仍未恢复，放弃")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

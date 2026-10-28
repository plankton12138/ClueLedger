"""S8 打包冒烟：启动 sidecar，检查健康、版本与静态页面，再关闭。

用法：uv run python scripts/smoke_packaged_server.py [exe路径]
默认检查 build-resources/sidecar/hastings-server.exe 与 frontend/dist。
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "build-resources" / "sidecar" / "hastings-server.exe"
STATIC = ROOT / "frontend" / "dist"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def get(url: str, timeout: float = 3.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:  # 4xx/5xx 也要给出状态
        return exc.code, exc.read().decode("utf-8", "replace")


def wait_health(base: str, deadline: float) -> dict[str, object]:
    last = "没有响应"
    while time.monotonic() < deadline:
        try:
            status, body = get(f"{base}/api/health")
        except OSError as exc:
            last = str(exc)
        else:
            if status == 200:
                return json.loads(body)
            last = f"HTTP {status}"
        time.sleep(0.3)
    raise RuntimeError(f"sidecar 没有就绪：{last}")


def main() -> int:
    exe = Path(sys.argv[1]) if len(sys.argv) > 1 else EXE
    if not exe.exists():
        raise SystemExit(f"找不到 sidecar：{exe}")
    if not (STATIC / "index.html").exists():
        raise SystemExit(f"找不到前端产物：{STATIC}")

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory() as folder:
        work = Path(folder)
        started = time.monotonic()
        process = subprocess.Popen(
            [
                str(exe),
                "--port",
                str(port),
                "--static-dir",
                str(STATIC),
                "--state-dir",
                str(work / "state"),
                "--library-dir",
                str(work / "library"),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            health = wait_health(base, time.monotonic() + 90)
            boot_ms = round((time.monotonic() - started) * 1000)
            status, index = get(f"{base}/")
            result = {
                "exe": str(exe),
                "exe_bytes": exe.stat().st_size,
                "port": port,
                "boot_ms": boot_ms,
                "health": health,
                "index_status": status,
                "index_has_product_name": "线簿" in index,
                "state_created": (work / "state").exists(),
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            ok = health.get("status") == "ok" and status == 200 and "线簿" in index
        finally:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:  # pragma: no cover - 仅在非 Windows 开发机上
                process.terminate()
            process.wait(timeout=15)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

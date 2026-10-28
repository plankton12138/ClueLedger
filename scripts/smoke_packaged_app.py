"""S8 打包冒烟（Electron 窗口）：启动已打包的桌面端，确认窗口真的加载了应用页面。

用法：uv run python scripts/smoke_packaged_app.py [exe路径]
使用临时 user-data-dir，避免动到真实的 %APPDATA%/Hastings。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "desktop" / "dist" / "win-unpacked" / "ClueLedger.exe"
CDP_TIMEOUT = 90


def cdp_pages(port: int) -> list[dict[str, object]]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


async def evaluate(ws_url: str, expression: str) -> object:
    async with websockets.connect(ws_url, max_size=8 * 1024 * 1024) as ws:
        await ws.send(
            json.dumps(
                {"id": 1, "method": "Runtime.evaluate", "params": {"expression": expression}}
            )
        )
        while True:
            message = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if message.get("id") == 1:
                return message["result"]["result"].get("value")


async def screenshot(ws_url: str, path: Path) -> None:
    async with websockets.connect(ws_url, max_size=32 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Page.captureScreenshot", "params": {}}))
        while True:
            message = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
            if message.get("id") == 1:
                import base64

                path.write_bytes(base64.b64decode(message["result"]["data"]))
                return


def main() -> int:
    exe = Path(sys.argv[1]) if len(sys.argv) > 1 else EXE
    if not exe.exists():
        raise SystemExit(f"找不到桌面端：{exe}")
    with tempfile.TemporaryDirectory() as folder:
        # 通过 APPDATA 重定向 userData，避免动到真实的 %APPDATA%/Hastings；
        # 桌面端自身用 app.setName("Hastings") 决定目录名。
        profile = Path(folder) / "appdata"
        profile.mkdir(parents=True, exist_ok=True)
        log = Path(folder) / "desktop.log"
        with_switch = subprocess.Popen(
            [str(exe)],
            env={**os.environ, "HASTINGS_CDP": "9223", "APPDATA": str(profile)},
            stdout=log.open("wb"),
            stderr=subprocess.STDOUT,
        )
        try:
            page: dict[str, object] | None = None
            deadline = time.monotonic() + CDP_TIMEOUT
            while time.monotonic() < deadline:
                try:
                    pages = [item for item in cdp_pages(9223) if item.get("type") == "page"]
                except OSError:
                    pages = []
                page = next((item for item in pages if "127.0.0.1" in str(item.get("url"))), None)
                if page is not None:
                    break
                time.sleep(0.5)
            if page is None:
                detail = log.read_text(encoding="utf-8", errors="replace")[-1500:]
                raise SystemExit(f"桌面窗口没有加载应用页面；日志：{detail}")

            title = asyncio.run(evaluate(str(page["webSocketDebuggerUrl"]), "document.title"))
            heading = asyncio.run(
                evaluate(
                    str(page["webSocketDebuggerUrl"]),
                    "document.querySelector('header')?.innerText ?? ''",
                )
            )
            shot = ROOT / "dev-notes" / "v1.0" / "artifacts" / "s8" / "packaged-app.png"
            shot.parent.mkdir(parents=True, exist_ok=True)
            asyncio.run(screenshot(str(page["webSocketDebuggerUrl"]), shot))
            result = {
                "exe": str(exe),
                "url": page["url"],
                "title": title,
                "header": str(heading).strip().splitlines()[:2],
                "screenshot": str(shot),
                "profile_used": str(profile),
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            ok = "线簿" in str(title) and str(page["url"]).startswith("http://127.0.0.1")
        finally:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(with_switch.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            with_switch.wait(timeout=20)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

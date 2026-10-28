"""S8 打包冒烟（重启）：同一份状态在两次启动、两个端口之间保留登记与草稿。

用法：uv run python scripts/smoke_packaged_restart.py [exe路径]
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from smoke_packaged_server import STATIC, free_port, get, wait_health

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "build-resources" / "sidecar" / "hastings-server.exe"


def post(url: str, payload: dict[str, object], method: str = "POST") -> tuple[int, str]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def start(exe: Path, state: Path, library: Path) -> tuple[subprocess.Popen[bytes], str, int]:
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [
            str(exe),
            "--port",
            str(port),
            "--static-dir",
            str(STATIC),
            "--state-dir",
            str(state),
            "--library-dir",
            str(library),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    wait_health(base, time.monotonic() + 90)
    return process, base, port


def stop(process: subprocess.Popen[bytes]) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(process.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:  # pragma: no cover - 非 Windows 开发机
        process.terminate()
    process.wait(timeout=15)


def main() -> int:
    exe = Path(sys.argv[1]) if len(sys.argv) > 1 else EXE
    if not exe.exists():
        raise SystemExit(f"找不到 sidecar：{exe}")
    with tempfile.TemporaryDirectory() as folder:
        work = Path(folder)
        state, library = work / "state", work / "library"
        book = work / "书架" / "重启验收"
        book.parent.mkdir(parents=True)

        first, base, first_port = start(exe, state, library)
        try:
            status, body = post(f"{base}/api/file-workspaces", {"path": str(book), "create": True})
            assert status == 200, body
            workspace_id = str(json.loads(body)["id"])
            status, body = post(
                f"{base}/api/file-workspaces/{workspace_id}/conversation-ui",
                {"set_draft": True, "draft_target": "new", "draft_text": "重启后要还在的草稿。"},
                method="PATCH",
            )
            assert status == 200, body
        finally:
            stop(first)

        second, base, second_port = start(exe, state, library)
        try:
            status, body = get(f"{base}/api/file-workspaces")
            assert status == 200, body
            listed = json.loads(body)
            status, body = get(f"{base}/api/file-workspaces/{workspace_id}/conversation-ui")
            assert status == 200, body
            draft = json.loads(body)
            result = {
                "exe": str(exe),
                "ports": [first_port, second_port],
                "dynamic_port_changed": first_port != second_port,
                "workspaces_after_restart": [item["id"] for item in listed],
                "draft_after_restart": draft.get("draft_text") or draft.get("drafts"),
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            ok = (
                first_port != second_port
                and [item["id"] for item in listed] == [workspace_id]
                and "重启后要还在的草稿。" in json.dumps(draft, ensure_ascii=False)
            )
        finally:
            stop(second)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""V04-10 真实验证：连续讨论、切换与新会话隔离。

用独立配置副本与独立测试作品，不碰作者自己的作品与状态目录。

用法（仓库根目录）::

    uv run python -m hastings.server --config data/v04-10-verify/config.yaml \
        --state-dir data/v04-10-verify/state --port 8766
    uv run python dev-notes/v0.4/artifacts/v04-10-verify.py --phase main
    # 重启服务后
    uv run python dev-notes/v0.4/artifacts/v04-10-verify.py --phase restart
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx

BOOK = "data/v04-10-verify/testbook"


def _wait(client: httpx.Client, workspace: str, conversation: str, *, turns: int) -> list[dict]:
    deadline = time.monotonic() + 420
    url = f"/api/file-workspaces/{workspace}/conversations/{conversation}/messages"
    while time.monotonic() < deadline:
        events = client.get(url).json()["events"]
        finished = [event for event in events if event["type"] == "run_finished"]
        if len(finished) >= turns:
            return list(events)
        time.sleep(1.0)
    raise SystemExit(f"等待第 {turns} 轮结束超时")


def _reply(events: list[dict]) -> str:
    texts = [event["text"] for event in events if event["type"] == "assistant_message"]
    return texts[-1] if texts else "（没有公开回复）"


def _result(events: list[dict]) -> dict:
    finished = [event for event in events if event["type"] == "run_finished"]
    return finished[-1] if finished else {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8766")
    parser.add_argument("--phase", choices=("main", "restart"), default="main")
    args = parser.parse_args()

    # 本机地址绕过系统代理：否则请求会被 HTTP_PROXY 接手。
    client = httpx.Client(base_url=args.base, timeout=600, trust_env=False)
    workspace = client.post(
        "/api/file-workspaces", json={"path": BOOK, "create": args.phase == "main"}
    )
    if workspace.status_code != 200:
        print("打开作品失败：", workspace.status_code, workspace.text[:300])
        return 1
    workspace_id = workspace.json()["id"]

    if args.phase == "main":
        listing = client.get(f"/api/file-workspaces/{workspace_id}/conversations").json()
        print("初始会话数：", len(listing["items"]), "｜活动轮次：", listing["active_turn"])
        first = client.post(
            f"/api/file-workspaces/{workspace_id}/conversations",
            json={
                "text": "记住一条创作约束：这本书不允许出现超自然解释，所有怪事都必须有人为原因。",
                "request_key": "v410-a1",
            },
        )
        print("首次发送：", first.status_code, first.text[:160])
        if first.status_code != 202:
            return 1
        body = first.json()
        conversation = body["conversation_id"]
        events = _wait(client, workspace_id, conversation, turns=1)
        print("第一轮状态：", _result(events).get("status"))
        print("第一轮回复：", _reply(events)[:160])

        second = client.post(
            f"/api/file-workspaces/{workspace_id}/conversations/{conversation}/turns",
            json={
                "text": "按刚才那条约束，写第一章的开头，雨夜，侦探刚到旅馆。",
                "request_key": "v410-a2",
            },
        )
        print("第二轮受理：", second.status_code, second.text[:120])
        events = _wait(client, workspace_id, conversation, turns=2)
        result = _result(events)
        print("第二轮状态：", result.get("status"), "｜记录：", result.get("recording"))
        print("第二轮改动：", result.get("changed_paths"))
        chapters = sorted(Path(BOOK, "正文").glob("*.md")) if Path(BOOK, "正文").is_dir() else []
        print("章节文件：", [path.name for path in chapters])

        listing = client.get(f"/api/file-workspaces/{workspace_id}/conversations").json()
        print("会话数：", len(listing["items"]), "｜最近：", listing["recent_id"] == conversation)
        print("会话标题：", [item["title"] for item in listing["items"]])
        return 0

    # restart：重启后继续同一个会话，再开 B 会话检查隔离。
    listing = client.get(f"/api/file-workspaces/{workspace_id}/conversations").json()
    if not listing["items"]:
        print("重启后没有会话：状态目录不可用")
        return 1
    conversation = listing["recent_id"] or listing["items"][0]["id"]
    print("重启后会话数：", len(listing["items"]), "｜恢复会话：", conversation)
    print("重启后活动轮次：", listing["active_turn"])

    third = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation}/turns",
        json={
            "text": "我一开始定下的那条创作约束是什么？用一句话复述，不要改文件。",
            "request_key": "v410-a3",
        },
    )
    print("第三轮受理：", third.status_code, third.text[:120])
    events = _wait(client, workspace_id, conversation, turns=3)
    print("第三轮状态：", _result(events).get("status"))
    print("第三轮回复：", _reply(events)[:300])

    other = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations",
        json={
            "text": "这是另一个会话。你在这里能看到别的会话讨论过什么约束吗？只说你能看到的。",
            "request_key": "v410-b1",
        },
    )
    print("B 会话受理：", other.status_code, other.text[:120])
    b_id = other.json()["conversation_id"]
    b_events = _wait(client, workspace_id, b_id, turns=1)
    print("B 会话状态：", _result(b_events).get("status"))
    print("B 会话回复：", _reply(b_events)[:300])

    listing = client.get(f"/api/file-workspaces/{workspace_id}/conversations").json()
    print("会话数：", len(listing["items"]), "｜标题：", [item["title"] for item in listing["items"]])
    return 0


if __name__ == "__main__":
    sys.exit(main())

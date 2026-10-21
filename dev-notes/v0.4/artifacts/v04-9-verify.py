"""V04-9 真实验证脚本（一次运行一份证据，不参与产品运行）。

做的事与实际界面调用同一组接口：

1. 读连接列表与当前选择；
2. 用已保存的 DeepSeek 连接获取模型列表（真实 /models）；
3. 对同一个模型做响应测试与工具测试；
4. 新增一个连接（复用同一份凭据）→ 设为当前 → 发起一轮写作 → 检查章节文件与 Git 提交；
5. 删除临时连接，选择回到原连接。

凭据从系统凭据存储读取后只用于请求体，不打印、不落盘。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
import keyring
import yaml

PROMPT = "写第一章，标题「雨夜」，200 字左右：侦探在雨夜抵达一家小旅馆。"


def _config(path: Path) -> dict[str, object]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _stored_key(ref: str) -> str:
    secret = keyring.get_password("hastings", ref)
    if not secret:
        raise SystemExit(f"系统凭据存储里没有 {ref}")
    return secret


def _events(client: httpx.Client, workspace: str, conversation: str) -> list[dict[str, object]]:
    response = client.get(
        f"/api/file-workspaces/{workspace}/conversations/{conversation}/messages"
    )
    response.raise_for_status()
    return list(response.json()["events"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--phase", choices=["main", "restart"], default="main")
    args = parser.parse_args()

    # 本机地址绕过系统代理：否则请求会被 HTTP_PROXY 接手并返回 404。
    client = httpx.Client(base_url=args.base, timeout=60, trust_env=False)

    config = client.get("/api/model-connections").json()
    print("== 连接列表 ==")
    print(f"revision={config['revision']}")
    for item in config["connections"]:
        print(
            f"- {item['name']} | {item['display_name']} | {item['api']} | "
            f"{len(item['models'])} 模型 | has_key={item['has_key']} | "
            f"key={item['masked_key']} | 存储={item['credential_storage']}"
        )
    print(f"当前选择：{json.dumps(config['selection'], ensure_ascii=False)}")

    if args.phase == "restart":
        expected = json.loads((args.workspace / "expected-selection.json").read_text("utf-8"))
        assert config["selection"]["connection_id"] == expected["connection_id"], config["selection"]
        assert config["selection"]["model"] == expected["model"], config["selection"]
        print("重启后选择一致：", config["selection"]["connection_id"], config["selection"]["model"])
        return 0

    original = next(item for item in config["connections"] if item["has_key"])
    stored = _config(args.config)
    ref = str(stored["providers"][0]["credential_ref"])

    print("\n== 模型发现（真实接口） ==")
    found_response = client.post(
        "/api/model-connections/discover",
        json={
            "connection_id": original["name"],
            "catalog_id": original["catalog_id"] or "deepseek",
            "base_url": original["base_url"],
            "api": original["api"],
        },
    )
    if found_response.status_code != 200:
        print("发现请求失败：", found_response.status_code, found_response.text[:400])
        return 1
    found = found_response.json()
    print(f"code={found['code']} complete={found['complete']} 数量={len(found['models'])}")
    print("前 5 个：", found["models"][:5])
    assert found["code"] is None, found

    model = original["default_model"]
    print(f"\n== 响应测试 / 工具测试（模型 {model}） ==")
    for kind in ("response", "tool"):
        probe = client.post(
            "/api/model-connections/probe",
            json={
                "connection_id": original["name"],
                "catalog_id": original["catalog_id"] or "deepseek",
                "base_url": original["base_url"],
                "api": original["api"],
                "kind": kind,
                "model": model,
            },
        ).json()
        print(f"{kind}: response={probe['response']} tool_call={probe['tool_call']} {probe['message']}")
        assert probe["code"] is None, probe
    # 两条测试是独立事实：响应测试不该把工具测试也说成通过。
    assert probe["tool_call"] in {"passed", "failed", "untested"}

    print("\n== 新增连接（复用同一份凭据） ==")
    created = client.post(
        "/api/model-connections",
        json={
            "display_name": "验证用连接",
            "catalog_id": original["catalog_id"] or "deepseek",
            "base_url": original["base_url"],
            "api": original["api"],
            "models": [model],
            "default_model": model,
            "api_key": _stored_key(ref),
        },
        headers={"If-Match": config["revision"]},
    )
    created.raise_for_status()
    created_body = created.json()
    temporary = next(
        item for item in created_body["connections"] if item["display_name"] == "验证用连接"
    )
    print(f"新建 {temporary['name']} key={temporary['masked_key']} 选择未变：{created_body['selection']['source']}")

    selected = client.put(
        "/api/model-selection",
        json={"connection_id": temporary["name"], "model": model},
        headers={"If-Match": created_body["revision"]},
    )
    selected.raise_for_status()
    print("设为当前：", selected.json()["selection"])

    print("\n== 一轮写作（真实模型 → 文件 → Git） ==")
    workspace = client.post(
        "/api/file-workspaces", json={"path": str(args.workspace), "create": True}
    ).json()
    workspace_id = workspace["id"]
    conversation = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations", json={}
    ).json()["id"]
    started = client.post(
        f"/api/file-workspaces/{workspace_id}/conversations/{conversation}/turns",
        json={"text": PROMPT},
    )
    started.raise_for_status()

    deadline = time.monotonic() + 300
    events: list[dict[str, object]] = []
    while time.monotonic() < deadline:
        events = _events(client, workspace_id, conversation)
        if any(event["type"] == "run_finished" for event in events):
            break
        time.sleep(1)
    started_at = next(event for event in events if event["type"] == "turn_started")
    print("轮次快照：", json.dumps(
        {key: started_at.get(key) for key in ("connection_id", "connection_name", "model")},
        ensure_ascii=False,
    ))
    tools = [event["tool"] for event in events if event["type"] == "tool_finished"]
    print("工具过程：", tools)
    finished = next(event for event in events if event["type"] == "run_finished")
    print(
        "结果：",
        json.dumps(
            {key: finished.get(key) for key in ("status", "recording", "changed_paths", "commit")},
            ensure_ascii=False,
        ),
    )
    assert finished["status"] == "completed", finished
    assert finished["recording"] == "committed", finished

    files = client.get(f"/api/file-workspaces/{workspace_id}/files").json()
    print("章节：", [(item["path"], item["size"]) for item in files["chapters"]])
    log = client.get(f"/api/file-workspaces/{workspace_id}/git/log").json()["commits"]
    print("Git 提交：", [(item["commit"][:8], item["subject"]) for item in log[:3]])

    print("\n== 删除临时连接（选择被清空） ==")
    removed = client.delete(
        f"/api/model-connections/{temporary['name']}",
        headers={"If-Match": selected.json()["revision"]},
    )
    removed.raise_for_status()
    print("删除后选择：", removed.json()["selection"]["source"])

    restored = client.put(
        "/api/model-selection",
        json={"connection_id": original["name"], "model": model},
        headers={"If-Match": removed.json()["revision"]},
    )
    restored.raise_for_status()
    selection = restored.json()["selection"]
    print("恢复选择：", selection)
    (args.workspace / "expected-selection.json").write_text(
        json.dumps({"connection_id": selection["connection_id"], "model": selection["model"]}),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

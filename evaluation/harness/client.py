"""评测 HTTP 客户端：只走产品公开接口，不导入业务模块。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

import httpx


class EvalClientError(RuntimeError):
    """接口调用失败（网络、非 2xx、业务错误体）。"""


@dataclass(frozen=True)
class AcceptedTurn:
    workspace_id: str
    conversation_id: str
    turn_id: str


class EvalClient(Protocol):
    def open_workspace(self, path: str) -> str: ...

    def set_capture_inputs(self, workspace_id: str, enabled: bool) -> None: ...

    def start_turn(
        self,
        workspace_id: str,
        *,
        text: str,
        selected_path: str | None,
        conversation_id: str | None = None,
    ) -> AcceptedTurn: ...

    def poll_events(
        self,
        workspace_id: str,
        conversation_id: str,
        *,
        after_seq: int = 0,
    ) -> list[dict[str, Any]]: ...

    def read_turn_trace(
        self, workspace_id: str, conversation_id: str, turn_id: str
    ) -> dict[str, Any]: ...

    def read_attachment(self, workspace_id: str, key: str) -> Any: ...

    def close(self) -> None: ...


class HttpEvalClient:
    """对本地模式服务的 httpx 封装。"""

    def __init__(self, base_url: str, *, timeout: float = 60.0) -> None:
        # trust_env=False：忽略本机 HTTP(S)_PROXY / NO_PROXY，避免损坏的代理配置
        # 把 base_url 解析成 Invalid port: ':1]' 一类错误。
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            trust_env=False,
        )

    def close(self) -> None:
        self._client.close()

    def _json(self, response: httpx.Response) -> Any:
        if response.status_code >= 400:
            detail = response.text
            try:
                detail = response.json()
            except Exception:
                pass
            raise EvalClientError(f"HTTP {response.status_code}: {detail}")
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def open_workspace(self, path: str) -> str:
        body = self._json(
            self._client.post("/api/file-workspaces", json={"path": path})
        )
        return str(body["id"])

    def set_capture_inputs(self, workspace_id: str, enabled: bool) -> None:
        current = self._json(
            self._client.get(f"/api/file-workspaces/{workspace_id}/activity/settings")
        )
        retention = 30
        if isinstance(current, dict) and isinstance(current.get("retention_days"), int):
            retention = current["retention_days"]
        self._json(
            self._client.put(
                f"/api/file-workspaces/{workspace_id}/activity/settings",
                json={"capture_inputs": enabled, "retention_days": retention},
            )
        )

    def start_turn(
        self,
        workspace_id: str,
        *,
        text: str,
        selected_path: str | None,
        conversation_id: str | None = None,
    ) -> AcceptedTurn:
        context: dict[str, Any] = {}
        if selected_path:
            context["selected_path"] = selected_path
        request_key = f"eval-{uuid4().hex}"
        if conversation_id:
            body = self._json(
                self._client.post(
                    f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/turns",
                    json={
                        "text": text,
                        "request_key": request_key,
                        "purpose": "chat",
                        "context": context,
                    },
                )
            )
            return AcceptedTurn(
                workspace_id=workspace_id,
                conversation_id=str(body.get("conversation_id") or conversation_id),
                turn_id=str(body["turn_id"]),
            )
        body = self._json(
            self._client.post(
                f"/api/file-workspaces/{workspace_id}/conversations",
                json={
                    "text": text,
                    "request_key": request_key,
                    "purpose": "chat",
                    "context": context,
                },
            )
        )
        return AcceptedTurn(
            workspace_id=workspace_id,
            conversation_id=str(body["conversation_id"]),
            turn_id=str(body["turn_id"]),
        )

    def poll_events(
        self,
        workspace_id: str,
        conversation_id: str,
        *,
        after_seq: int = 0,
    ) -> list[dict[str, Any]]:
        body = self._json(
            self._client.get(
                f"/api/file-workspaces/{workspace_id}/conversations/{conversation_id}/messages",
                params={"after": after_seq},
            )
        )
        events = body.get("events") if isinstance(body, dict) else None
        if not isinstance(events, list):
            return []
        return [item for item in events if isinstance(item, dict)]

    def read_turn_trace(
        self, workspace_id: str, conversation_id: str, turn_id: str
    ) -> dict[str, Any]:
        body = self._json(
            self._client.get(
                f"/api/file-workspaces/{workspace_id}/conversations/"
                f"{conversation_id}/turns/{turn_id}/trace"
            )
        )
        if not isinstance(body, dict):
            raise EvalClientError("Trace 响应不是对象")
        return body

    def read_attachment(self, workspace_id: str, key: str) -> Any:
        return self._json(
            self._client.get(
                f"/api/file-workspaces/{workspace_id}/activity/attachments/{key}"
            )
        )

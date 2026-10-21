"""模型增量到会话事件的桥接（V05-2A）。

供应商返回的是一串增量事件：思考开始、思考片段、文字片段、文字结束、响应结束。
会话日志要记的是「作者读到的过程」：哪一次响应的哪个内容块、按什么顺序、逐段长了
多少字、最后是否完整。这一层只做翻译，不改上游循环，也不在事件里塞整份模型响应。

几条边界：

- **先保存，再发布**：增量先进缓冲，按「约 100 毫秒或约 1,024 字符」合批写盘，
  写完才让事件流读到。界面上出现的字，刷新后一定还在。
- **完整值是校对依据**：块结束时用模型返回的完整文本覆盖已收增量，不把两者再拼一遍；
  一个响应最多保存一条正式公开文本（``assistant_message``），供复制与后续上下文使用。
- **只存能展示的内容**：思考文本与耗时落地，签名、加密字段、redacted 内容与完整原始
  响应一律不写；空块不生成事件。
- **终态各自成立**：块与响应只说明自己的状态（完成/停止/失败/中断/截断），不代替整轮
  结果；整轮是否成功由 ``run_finished`` 说明。
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from hastings.workspace.state import ConversationStore
from tau_agent.messages import AssistantMessage, TextContent, ThinkingContent

__all__ = [
    "ChatStreamBridge",
    "Completion",
    "FLUSH_CHARS",
    "FLUSH_INTERVAL",
    "public_text",
    "repair_interrupted_stream",
]

Completion = Literal["completed", "stopped", "failed", "interrupted", "truncated"]
BlockKind = Literal["text", "thinking"]

#: 合批阈值：先到者触发。等待时间从第一段未提交内容算起，不是从上一次提交算起。
FLUSH_INTERVAL = 0.1
FLUSH_CHARS = 1024

#: 公开文本块的拼接方式：界面上每块单独成段，复制与上下文用同一规则。
_TEXT_JOINER = "\n\n"

_COMPLETION_BY_STOP_REASON: dict[str, Completion] = {
    "stop": "completed",
    "toolUse": "completed",
    "length": "truncated",
    "aborted": "stopped",
    "error": "failed",
}


@dataclass
class _Block:
    """一个内容块的展示状态：顺序、已收文本、已发片段数。"""

    block_id: str
    kind: BlockKind
    order: int
    started_at: float
    text: str = ""
    parts: int = 0
    finished: bool = False

    def to_event(self, message_id: str, turn_id: str, text: str, completion: Completion,
                 duration_ms: int) -> dict[str, Any]:
        return {
            "type": "assistant_block_finished",
            "turn_id": turn_id,
            "message_id": message_id,
            "block_id": self.block_id,
            "kind": self.kind,
            "order": self.order,
            "text": text,
            "last_part": self.parts - 1,
            "completion": completion,
            "duration_ms": max(0, duration_ms),
        }


@dataclass
class _Response:
    """一次模型响应的展示状态。"""

    message_id: str
    started_at: float
    blocks: dict[str, _Block] = field(default_factory=dict)
    public_saved: bool = False
    finished: bool = False


def public_text(blocks: list[_Block]) -> str:
    """按内容位置拼出该响应的公开文本；思考块不参与。"""
    ordered = sorted(
        (block for block in blocks if block.kind == "text" and block.text.strip()),
        key=lambda block: block.order,
    )
    return _TEXT_JOINER.join(block.text for block in ordered)


class ChatStreamBridge:
    """把一次轮次里的模型事件翻译成会话事件，并负责合批与封口。"""

    def __init__(
        self,
        store: ConversationStore,
        conversation_id: str,
        turn_id: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        flush_interval: float = FLUSH_INTERVAL,
        flush_chars: int = FLUSH_CHARS,
    ) -> None:
        self.store = store
        self.conversation_id = conversation_id
        self.turn_id = turn_id
        self._clock = clock
        self.flush_interval = flush_interval
        self.flush_chars = flush_chars
        self._buffer: list[dict[str, Any]] = []
        self._buffered_chars = 0
        self._pending_since: float | None = None
        self._response: _Response | None = None
        self._phase = ""

    # -- 发布 ---------------------------------------------------------------

    def flush(self) -> None:
        """提交缓冲区；写盘失败时抛错，由调用方结束本轮。"""
        if not self._buffer:
            return
        events, self._buffer = self._buffer, []
        self._buffered_chars = 0
        self._pending_since = None
        self.store.append_batch(self.conversation_id, events, update_activity=False)

    def flush_due(self) -> None:
        """到点或积压够多才提交；空闲定时器与事件循环都可以调用。"""
        if not self._buffer:
            return
        if self._buffered_chars >= self.flush_chars or self.pending_delay() <= 0:
            self.flush()

    def pending_delay(self) -> float:
        """距离下一次应当提交还有多久；没有待提交内容时返回整段间隔。"""
        if self._pending_since is None:
            return self.flush_interval
        elapsed = self._clock() - self._pending_since
        return max(0.0, self.flush_interval - elapsed)

    def phase(self, name: str) -> None:
        """记录应用确认的运行阶段；连续相同阶段不重复写。"""
        if name == self._phase:
            return
        self._phase = name
        self._push({"type": "run_phase", "phase": name})

    def _push(self, event: dict[str, Any]) -> None:
        event.setdefault("turn_id", self.turn_id)
        self._buffer.append(event)
        text = event.get("text")
        if isinstance(text, str):
            self._buffered_chars += len(text)
        if self._pending_since is None:
            self._pending_since = self._clock()

    # -- 响应与块 -----------------------------------------------------------

    def start_response(self) -> str:
        """开始一次响应；已有未结束的响应时沿用它（开始事件可能迟到）。"""
        response = self._open_response()
        if response is not None:
            return response.message_id
        response = _Response(message_id=f"m_{os.urandom(6).hex()}", started_at=self._clock())
        self._response = response
        self.phase("model")
        self._push({"type": "assistant_started", "message_id": response.message_id})
        return response.message_id

    def append(self, kind: BlockKind, content_index: int, delta: str) -> None:
        """追加一段真实增量；空片段不生成事件。"""
        if not delta:
            return
        response = self._ensure_response()
        block = self._block_at(response, kind, content_index, create=True)
        if block is None:
            return
        part = block.parts
        block.parts += 1
        block.text += delta
        self._push(
            {
                "type": "assistant_block_delta",
                "message_id": response.message_id,
                "block_id": block.block_id,
                "kind": kind,
                "order": block.order,
                "part": part,
                "text": delta,
            }
        )

    def finish_block(self, kind: BlockKind, content_index: int, text: str) -> None:
        """段结束：用完整内容封口；没有内容的块不展示。"""
        response = self._ensure_response()
        block = self._block_at(response, kind, content_index, create=False)
        if block is None:
            if not text.strip():
                return
            block = self._block_at(response, kind, content_index, create=True)
            assert block is not None
        self._close_block(response, block, text, "completed")

    def finish_response(
        self, message: AssistantMessage, *, completion: Completion | None = None
    ) -> Completion:
        """响应结束：按完整消息校对块，写出正式公开文本，封口本次响应。"""
        response = self._ensure_response()
        resolved = completion or self.completion_of(message)
        covered: set[str] = set()
        for index, part in enumerate(message.content):
            if isinstance(part, TextContent):
                block = self._block_at(response, "text", index, create=bool(part.text.strip()))
                if block is None:
                    continue
                covered.add(block.block_id)
                self._close_block(response, block, part.text, resolved)
            elif isinstance(part, ThinkingContent):
                # redacted 的思考只有加密占位，不落地、不展示。
                if part.redacted:
                    continue
                block = self._block_at(
                    response, "thinking", index, create=bool(part.thinking.strip())
                )
                if block is None:
                    continue
                covered.add(block.block_id)
                self._close_block(response, block, part.thinking, resolved)
        # 完整消息里没有、但我们收到过增量的块：用已收文本封口，内容不丢。
        for block in list(response.blocks.values()):
            if block.block_id in covered:
                continue
            self._close_block(response, block, block.text, resolved)
        self._save_public_text(response, resolved)
        response.finished = True
        self._push(
            {
                "type": "assistant_finished",
                "message_id": response.message_id,
                "completion": resolved,
                "stop_reason": message.stop_reason,
                "duration_ms": self._elapsed_ms(response.started_at),
            }
        )
        return resolved

    def close_open(self, completion: Completion) -> None:
        """提交缓冲并按顺序封口尚未结束的块与响应（停止、失败、进程退出前）。"""
        response = self._response
        if response is None or response.finished:
            self.flush()
            return
        for block in sorted(response.blocks.values(), key=lambda item: item.order):
            self._close_block(response, block, block.text, completion)
        self._save_public_text(response, completion)
        response.finished = True
        self._push(
            {
                "type": "assistant_finished",
                "message_id": response.message_id,
                "completion": completion,
                "stop_reason": "",
                "duration_ms": self._elapsed_ms(response.started_at),
            }
        )
        self.flush()

    # -- 内部 ---------------------------------------------------------------

    @staticmethod
    def completion_of(message: AssistantMessage) -> Completion:
        return _COMPLETION_BY_STOP_REASON.get(message.stop_reason, "completed")

    def _open_response(self) -> _Response | None:
        response = self._response
        if response is not None and not response.finished:
            return response
        return None

    def _ensure_response(self) -> _Response:
        open_response = self._open_response()
        if open_response is not None:
            return open_response
        message_id = self.start_response()
        assert self._response is not None and self._response.message_id == message_id
        return self._response

    def _block_at(
        self, response: _Response, kind: BlockKind, content_index: int, *, create: bool
    ) -> _Block | None:
        block_id = f"{response.message_id}:{kind}:{content_index}"
        block = response.blocks.get(block_id)
        if block is not None or not create:
            return block
        block = _Block(
            block_id=block_id, kind=kind, order=content_index, started_at=self._clock()
        )
        response.blocks[block_id] = block
        self._push(
            {
                "type": "assistant_block_started",
                "message_id": response.message_id,
                "block_id": block_id,
                "kind": kind,
                "order": content_index,
            }
        )
        return block

    def _close_block(
        self, response: _Response, block: _Block, text: str, completion: Completion
    ) -> None:
        if block.finished:
            return
        block.finished = True
        block.text = text
        duration = self._elapsed_ms(block.started_at)
        self._push(block.to_event(response.message_id, self.turn_id, text, completion, duration))

    def _save_public_text(self, response: _Response, completion: Completion) -> None:
        if response.public_saved:
            return
        text = public_text(list(response.blocks.values()))
        if not text.strip():
            # 没有公开文本的响应只保留过程状态，不生成空回复。
            return
        response.public_saved = True
        self._push(
            {
                "type": "assistant_message",
                "message_id": response.message_id,
                "text": text,
                "completion": completion,
            }
        )

    def _elapsed_ms(self, started_at: float) -> int:
        return int(max(0.0, self._clock() - started_at) * 1000)


# -- 中断修复 ---------------------------------------------------------------


@dataclass
class _ReplayBlock:
    kind: str
    order: int
    text: str = ""
    parts: int = 0
    finished: bool = False


def repair_interrupted_stream(
    store: ConversationStore, conversation_id: str, turn_id: str
) -> bool:
    """重启后补齐没有结束的响应：重建块、补部分公开文本、补响应终态。

    只看日志里已有的内容：没有存下来的增量无法恢复，也不猜。修复本身是幂等的，
    因为补上的 ``assistant_finished`` 会让下一次扫描认为这次响应已经结束。
    """
    responses: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for event in store.events(conversation_id):
        if event.get("turn_id") != turn_id:
            continue
        kind = event.get("type")
        message_id = event.get("message_id")
        if kind == "assistant_started" and isinstance(message_id, str):
            if message_id not in responses:
                responses[message_id] = {"blocks": {}, "order": [], "public": False, "done": False}
                order.append(message_id)
        elif kind == "assistant_block_started" and isinstance(message_id, str):
            entry = responses.get(message_id)
            if entry is None:
                entry = {"blocks": {}, "order": [], "public": False, "done": False}
                responses[message_id] = entry
                order.append(message_id)
            block_id = event.get("block_id")
            if isinstance(block_id, str):
                entry["blocks"][block_id] = _ReplayBlock(
                    kind=str(event.get("kind", "text")),
                    order=int(event.get("order", len(entry["blocks"]))),
                )
                entry["order"].append(block_id)
        elif kind == "assistant_block_delta" and isinstance(message_id, str):
            entry = responses.get(message_id)
            block_id = event.get("block_id")
            if entry is None or not isinstance(block_id, str):
                continue
            block = entry["blocks"].get(block_id)
            if block is None or block.finished:
                continue
            text = event.get("text")
            if isinstance(text, str):
                block.text += text
            block.parts += 1
        elif kind == "assistant_block_finished" and isinstance(message_id, str):
            entry = responses.get(message_id)
            block_id = event.get("block_id")
            if entry is None or not isinstance(block_id, str):
                continue
            block = entry["blocks"].get(block_id)
            if block is None:
                continue
            text = event.get("text")
            if isinstance(text, str):
                block.text = text
            block.finished = True
        elif kind == "assistant_message" and isinstance(message_id, str):
            entry = responses.get(message_id)
            if entry is not None:
                entry["public"] = True
        elif kind == "assistant_finished" and isinstance(message_id, str):
            entry = responses.get(message_id)
            if entry is not None:
                entry["done"] = True

    repaired = False
    for message_id in order:
        entry = responses[message_id]
        if entry["done"]:
            continue
        records: list[dict[str, Any]] = []
        for block_id in entry["order"]:
            block = entry["blocks"][block_id]
            if block.finished:
                continue
            block.finished = True
            records.append(
                {
                    "type": "assistant_block_finished",
                    "turn_id": turn_id,
                    "message_id": message_id,
                    "block_id": block_id,
                    "kind": block.kind,
                    "order": block.order,
                    "text": block.text,
                    "last_part": block.parts - 1,
                    "completion": "interrupted",
                    "duration_ms": None,
                }
            )
        text = _TEXT_JOINER.join(
            entry["blocks"][block_id].text
            for block_id in entry["order"]
            if entry["blocks"][block_id].kind == "text" and entry["blocks"][block_id].text.strip()
        )
        if text and not entry["public"]:
            records.append(
                {
                    "type": "assistant_message",
                    "turn_id": turn_id,
                    "message_id": message_id,
                    "text": text,
                    "completion": "interrupted",
                }
            )
        records.append(
            {
                "type": "assistant_finished",
                "turn_id": turn_id,
                "message_id": message_id,
                "completion": "interrupted",
                "stop_reason": "",
                "duration_ms": None,
            }
        )
        store.append_batch(conversation_id, records, update_activity=False)
        repaired = True
    return repaired

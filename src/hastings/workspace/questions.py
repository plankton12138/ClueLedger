"""作者问题的持久记录（0.8 模型自主维护模块）。

问题不是普通聊天消息：它是「这一轮停下来等作者决定」的唯一凭据。模型调用
``ask_author`` 时写入一条记录并结束本轮；作者回答时按 ``question_id`` 找回问题，
核对会话归属与目标版本，再开启新轮次承接原请求。

记录放在作品状态目录，不进作品 Git：问题属于交互过程，不是作品内容。读取损坏按空
处理，写入整份替换；已回答的记录保留，便于刷新后仍能看到当时的问答。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.state_store import read_records, write_records

__all__ = ["AuthorQuestion", "QuestionStore"]

_RECORD_VERSION = 1
_RECIPE_VERSION = 1
_QUESTIONS_KEY = "questions"
_FILE_NAME = "questions.json"
_ID_PREFIX = "q_"
_MAX_QUESTION = 2000
_MAX_OPTION = 120
_MAX_OPTIONS = 5


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _text(value: object, limit: int = 0) -> str:
    text = str(value or "").strip()
    return text if limit <= 0 else text[:limit]


def _texts(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(_text(item) for item in value if _text(item))


@dataclass(frozen=True, slots=True)
class AuthorQuestion:
    """一条待答或已回答的作者问题；``context`` 保存原请求的材料身份。"""

    question_id: str
    conversation_id: str
    turn_id: str
    question: str
    options: tuple[str, ...] = ()
    related: tuple[str, ...] = ()
    created_at: str = ""
    assembly_digest: str = ""
    context: Mapping[str, Any] = field(default_factory=dict)
    answer: str = ""
    answered_at: str = ""
    answered_turn_id: str = ""

    @property
    def answered(self) -> bool:
        return bool(self.answered_at)

    def view(self) -> dict[str, object]:
        """事件与接口共用的一份公开事实；不含内部材料身份。"""
        return {
            "question_id": self.question_id,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "question": self.question,
            "options": list(self.options),
            "related": list(self.related),
            "created_at": self.created_at,
            "answered": self.answered,
            "answer": self.answer,
            "answered_at": self.answered_at,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "question_id": self.question_id,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "question": self.question,
            "options": list(self.options),
            "related": list(self.related),
            "created_at": self.created_at,
            "assembly_digest": self.assembly_digest,
            "context": dict(self.context),
            "answer": self.answer,
            "answered_at": self.answered_at,
            "answered_turn_id": self.answered_turn_id,
        }

    @classmethod
    def from_dict(cls, item: Mapping[str, Any]) -> AuthorQuestion | None:
        question_id = _text(item.get("question_id"))
        conversation_id = _text(item.get("conversation_id"))
        if not question_id or not conversation_id:
            return None
        context = item.get("context")
        return cls(
            question_id=question_id,
            conversation_id=conversation_id,
            turn_id=_text(item.get("turn_id")),
            question=_text(item.get("question")),
            options=_texts(item.get("options")),
            related=_texts(item.get("related")),
            created_at=_text(item.get("created_at")),
            assembly_digest=_text(item.get("assembly_digest")),
            context=dict(context) if isinstance(context, dict) else {},
            answer=_text(item.get("answer")),
            answered_at=_text(item.get("answered_at")),
            answered_turn_id=_text(item.get("answered_turn_id")),
        )


class QuestionStore:
    """作品状态目录里的 ``questions.json``：整份替换，读取损坏按空处理。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.file = self.root / _FILE_NAME

    def records(self) -> tuple[AuthorQuestion, ...]:
        rows: list[AuthorQuestion] = []
        for item in read_records(
            self.file,
            record_version=_RECORD_VERSION,
            recipe_version=_RECIPE_VERSION,
            key=_QUESTIONS_KEY,
        ):
            parsed = AuthorQuestion.from_dict(item)
            if parsed is not None:
                rows.append(parsed)
        return tuple(rows)

    def get(self, question_id: str) -> AuthorQuestion | None:
        for item in self.records():
            if item.question_id == question_id:
                return item
        return None

    def pending(self, conversation_id: str) -> tuple[AuthorQuestion, ...]:
        return tuple(
            item
            for item in self.records()
            if item.conversation_id == conversation_id and not item.answered
        )

    def create(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        question: str,
        options: tuple[str, ...] = (),
        related: tuple[str, ...] = (),
        assembly_digest: str = "",
        context: Mapping[str, Any] | None = None,
    ) -> AuthorQuestion:
        clean = _text(question)
        if not clean:
            raise WorkspaceError(
                code="AUTHOR_QUESTION_INPUT_INVALID",
                where="questions.create",
                why="问题是空的",
                fix="写清需要作者决定什么，再调用 ask_author",
            )
        if len(clean) > _MAX_QUESTION:
            raise WorkspaceError(
                code="AUTHOR_QUESTION_INPUT_INVALID",
                where="questions.create",
                why=f"问题超过 {_MAX_QUESTION} 字",
                fix="只保留需要作者决定的那一句",
                details={"limit": _MAX_QUESTION, "length": len(clean)},
            )
        clean_options = tuple(_text(item, _MAX_OPTION) for item in options[:_MAX_OPTIONS])
        created = AuthorQuestion(
            question_id=_ID_PREFIX + os.urandom(8).hex(),
            conversation_id=conversation_id,
            turn_id=turn_id,
            question=clean,
            options=tuple(item for item in clean_options if item),
            related=tuple(item for item in related if item),
            created_at=_now(),
            assembly_digest=assembly_digest,
            context=dict(context or {}),
        )
        self._write((*self.records(), created))
        return created

    def answer(self, question_id: str, *, answer: str, turn_id: str) -> AuthorQuestion:
        items = list(self.records())
        for index, item in enumerate(items):
            if item.question_id != question_id:
                continue
            if item.answered:
                raise WorkspaceError(
                    code="AUTHOR_QUESTION_ANSWERED_CONFLICT",
                    where="questions.answer",
                    why="这个问题已经回答过",
                    fix="在对话里继续提出新的要求；不要重复点击旧问题的选项",
                    details={"question_id": question_id},
                )
            updated = replace(
                item,
                answer=_text(answer),
                answered_at=_now(),
                answered_turn_id=turn_id,
            )
            items[index] = updated
            self._write(tuple(items))
            return updated
        raise WorkspaceError(
            code="AUTHOR_QUESTION_NOT_FOUND",
            where="questions.answer",
            why=f"没有找到这个问题：{question_id}",
            fix="刷新对话后确认问题是否仍然有效",
            details={"question_id": question_id},
        )

    def _write(self, items: tuple[AuthorQuestion, ...]) -> None:
        write_records(
            self.file,
            prefix=".questions-",
            record_version=_RECORD_VERSION,
            recipe_version=_RECIPE_VERSION,
            key=_QUESTIONS_KEY,
            entries=[item.to_dict() for item in items],
        )

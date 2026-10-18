"""试写候选（V03-3.3/3.4）：一次起草的输入、输出与只读预览口径。

三条边界，全部由纯函数保证：

- **候选不是项目真相**：候选只存在于辅助表，采用之前不产生任何提交，
  也不改正式正文（计划 §3.3「所有正文写入共用一条管线」的入口仍然唯一）。
- **所见即所采**：``preview_hash`` 覆盖候选身份、修订号、标题、正文与生成时的
  基础版本；采用必须携带同一哈希，服务端再算一次，不一致就拒绝。
- **迟到候选不覆盖新内容**：只有 ``revision == current_revision`` 的候选可被
  采用；重新生成会推进修订号，旧修订的哈希自然失效。

提示词是确定性拼接：同样的 brief 得到同样的文本，便于测试与事后追溯。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from hastings.domain.repository.serialization import content_hash

__all__ = [
    "CANDIDATE_ADOPTED",
    "CANDIDATE_DISCARDED",
    "CANDIDATE_READY",
    "CANDIDATE_SUPERSEDED",
    "ChapterCandidate",
    "DraftBrief",
    "clean_chapter_text",
    "preview_hash",
    "render_system_prompt",
    "render_user_prompt",
]

# 候选状态：可预览/采用 → 被新修订取代 → 已采用 / 已丢弃。
CANDIDATE_READY = "ready"
CANDIDATE_SUPERSEDED = "superseded"
CANDIDATE_ADOPTED = "adopted"
CANDIDATE_DISCARDED = "discarded"

#: 目标写「新章」时使用；其余取值是现有章节 ID。
TARGET_NEW_CHAPTER = "new"

# 单次候选的正文上限（与导入的章上限同一量级；超出要求拆分）。
MAX_CANDIDATE_CHARS = 120_000

_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*\n(.*?)\n\s*```\s*$", re.S)
_LEADING_TITLE = re.compile(r"^\s*#{1,3}\s*第?[^\n]{0,40}章[^\n]*\n+")
_TRAILING_NOTES = re.compile(r"\n\s*(?:---+\s*\n)?\s*(?:说明|备注|注)[:：][\s\S]*$")


def clean_chapter_text(raw: str) -> str:
    """把模型输出整理成可入库正文：去围栏、去标题行、去尾部说明。

    只做结构性清理，不改写内容——「采用文本 = 所见文本」要求清理后的结果
    就是预览里展示、也是最终提交的那一份。
    """
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1)
    text = _LEADING_TITLE.sub("", text)
    text = _TRAILING_NOTES.sub("", text)
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


@dataclass(frozen=True, slots=True)
class DraftBrief:
    """起草输入：账本引用 + 作者要求 + 视角 + 目标 + 长度 + 叙事边界。

    ``base_head`` 是生成时项目的最新提交，可以为 ``None``（空项目首章）。
    ``base_chapter`` 仅对「改写现有章」有意义，记录生成时该章的基础版本。
    """

    brief_id: str
    project_id: str
    target: str = TARGET_NEW_CHAPTER
    chapter_title: str = ""
    instruction: str = ""
    viewpoint: str = ""
    length_target: str = ""
    narrative_boundary: str = ""
    refs: tuple[str, ...] = ()
    base_head: str | None = None
    base_chapter: str | None = None
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "brief_id": self.brief_id,
            "project_id": self.project_id,
            "target": self.target,
            "chapter_title": self.chapter_title,
            "instruction": self.instruction,
            "viewpoint": self.viewpoint,
            "length_target": self.length_target,
            "narrative_boundary": self.narrative_boundary,
            "refs": list(self.refs),
            "base_head": self.base_head,
            "base_chapter": self.base_chapter,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DraftBrief:
        return cls(
            brief_id=str(data["brief_id"]),
            project_id=str(data["project_id"]),
            target=str(data.get("target") or TARGET_NEW_CHAPTER),
            chapter_title=str(data.get("chapter_title", "")),
            instruction=str(data.get("instruction", "")),
            viewpoint=str(data.get("viewpoint", "")),
            length_target=str(data.get("length_target", "")),
            narrative_boundary=str(data.get("narrative_boundary", "")),
            refs=tuple(str(r) for r in data.get("refs", [])),
            base_head=data.get("base_head"),
            base_chapter=data.get("base_chapter"),
            created_at=str(data.get("created_at", "")),
        )


@dataclass(frozen=True, slots=True)
class CandidateRevision:
    """候选的一版正文；修订号在同一个候选内单调递增。"""

    revision: int
    title: str
    text: str
    preview_hash: str
    instruction: str = ""
    created_at: str = ""
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "title": self.title,
            "text": self.text,
            "preview_hash": self.preview_hash,
            "instruction": self.instruction,
            "created_at": self.created_at,
            "model": self.model,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CandidateRevision:
        return cls(
            revision=int(data["revision"]),
            title=str(data.get("title", "")),
            text=str(data.get("text", "")),
            preview_hash=str(data["preview_hash"]),
            instruction=str(data.get("instruction", "")),
            created_at=str(data.get("created_at", "")),
            model=str(data.get("model", "")),
        )


def preview_hash(
    *,
    candidate_id: str,
    revision: int,
    title: str,
    text: str,
    base_head: str | None,
) -> str:
    """预览指纹：候选身份 + 修订 + 所见标题正文 + 生成基础。"""
    return content_hash(
        {
            "candidate_id": candidate_id,
            "revision": revision,
            "title": title,
            "text": text,
            "base_head": base_head,
        }
    )


@dataclass(frozen=True, slots=True)
class ChapterCandidate:
    """一份试写候选：brief + 全部修订 + 采用结果。"""

    candidate_id: str
    project_id: str
    brief: DraftBrief
    current_revision: int = 1
    status: str = CANDIDATE_READY
    revisions: tuple[CandidateRevision, ...] = field(default_factory=tuple)
    adopted_operation_id: str | None = None
    adopted_chapter_id: str | None = None
    adopted_revision: int | None = None
    created_at: str = ""
    updated_at: str = ""

    def revision(self, number: int | None = None) -> CandidateRevision | None:
        wanted = self.current_revision if number is None else number
        return next((r for r in self.revisions if r.revision == wanted), None)

    @property
    def latest(self) -> CandidateRevision | None:
        return self.revision(self.current_revision)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "project_id": self.project_id,
            "brief": self.brief.to_dict(),
            "current_revision": self.current_revision,
            "status": self.status,
            "revisions": [r.to_dict() for r in self.revisions],
            "adopted_operation_id": self.adopted_operation_id,
            "adopted_chapter_id": self.adopted_chapter_id,
            "adopted_revision": self.adopted_revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChapterCandidate:
        return cls(
            candidate_id=str(data["candidate_id"]),
            project_id=str(data["project_id"]),
            brief=DraftBrief.from_dict(dict(data["brief"])),
            current_revision=int(data.get("current_revision", 1)),
            status=str(data.get("status", CANDIDATE_READY)),
            revisions=tuple(
                CandidateRevision.from_dict(dict(r)) for r in data.get("revisions", [])
            ),
            adopted_operation_id=data.get("adopted_operation_id"),
            adopted_chapter_id=data.get("adopted_chapter_id"),
            adopted_revision=data.get("adopted_revision"),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )


# -- 提示词 -------------------------------------------------------------------

SYSTEM_PROMPT = (
    "你是小说正文作者。只输出这一章的正文：不要标题行、不要章节编号、"
    "不要解释创作思路、不要总结。段落之间空一行。"
    "严格使用给定的人物、地点与设定事实；设定没写的细节可以合理补充，"
    "但不得与已给事实冲突。作者没有决定的事（例如凶手是谁）不要替他决定，"
    "写成留有余地的叙述即可。"
)


def _section(heading: str, body: str) -> str:
    body = (body or "").strip()
    return f"【{heading}】\n{body}\n" if body else ""


def render_user_prompt(
    brief: DraftBrief,
    *,
    ledger: list[dict[str, str]] | None = None,
    previous_text: str = "",
    adjustment: str = "",
) -> str:
    """把 brief 渲染为确定性提示词；账本条目按传入顺序列出，不排序不猜。"""
    target = "新章" if brief.target == TARGET_NEW_CHAPTER else f"现有章节 {brief.target}"
    if brief.chapter_title:
        target += f"（标题：{brief.chapter_title}）"
    parts = [
        _section("目标", target),
        _section("作者要求", brief.instruction),
        _section("视角", brief.viewpoint),
        _section("篇幅", brief.length_target),
        _section("叙事边界", brief.narrative_boundary),
    ]
    if ledger:
        lines = [
            f"- {item.get('kind_label') or item.get('kind', '')} {item['title']}：{item['summary']}"
            for item in ledger
        ]
        parts.append(_section("本书设定", "\n".join(lines)))
    if previous_text.strip():
        parts.append(_section("上一稿正文", previous_text.strip()))
    if adjustment.strip():
        parts.append(_section("本次调整", adjustment.strip()))
    parts.append(
        _section(
            "写作规则",
            "只写这一章正文；不写标题行；不解释；不新增与设定冲突的事实；"
            "作者未决定的关键真相保持未定。",
        )
    )
    return "\n".join(p for p in parts if p).strip()


def render_system_prompt() -> str:
    return SYSTEM_PROMPT

"""章摘要（V03-4.3）：来源清单、确定性提示词与迟到结果裁决。

摘要是**派生资料**，不是正文事实：

- 一次生成冻结「来源清单」（章的 ``chapter_base`` + 每个活动段落的 revision）；
  结果落地前再取一次当前清单，不一致就判 ``superseded``，绝不覆盖新稿。
- 保存正文只把摘要标 ``stale``（V03-2.2 的失效裁决），**不会**删除已有摘要内容；
  重新生成只由作者显式启动，其他任务不得夹带（I06）。
- 提示词固定，输出要求「按要点、不确定就写未交代」，避免模型把推测写成事实。

纯函数：读目录、比较清单、拼内容；调用模型与提交在应用服务里。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.manuscript.evidence import KIND_CHAPTER_SUMMARY, SUMMARY_STATUS_FRESH

__all__ = [
    "KIND",
    "MAX_SUMMARY_CHARS",
    "SUMMARY_STATUS_SUPERSEDED",
    "SUMMARY_SYSTEM_PROMPT",
    "SourceManifest",
    "SummarySource",
    "build_content",
    "chapter_text",
    "decide",
    "manifest_from",
    "render_summary_prompt",
]

KIND = KIND_CHAPTER_SUMMARY

#: 结果被判「来源已变」时不落地，只把事实告诉作者（计划 §4.3）。
SUMMARY_STATUS_SUPERSEDED = "superseded"

#: 摘要正文上限：概览要装得下多章，单章摘要不能长篇复述。
MAX_SUMMARY_CHARS = 1200

SUMMARY_SYSTEM_PROMPT = (
    "你是作者的编辑助手，负责维护章节摘要。只根据给定正文归纳，不补充、"
    "不推测正文没有写出的事实；无法确定的地方写「未交代」。"
)


@dataclass(frozen=True, slots=True)
class SummarySource:
    """一个活动段落对摘要的贡献：稳定 ID + 当时的 Revision。"""

    unit_id: str
    revision_id: str

    def to_wire(self) -> dict[str, str]:
        return {"unit_id": self.unit_id, "revision_id": self.revision_id}


@dataclass(frozen=True, slots=True)
class SourceManifest:
    """生成时的来源清单：章结构基础 + 段落 revision 有序列表。"""

    chapter_id: str
    chapter_base: str
    units: tuple[SummarySource, ...] = ()

    def to_wire(self) -> dict[str, Any]:
        return {
            "chapter_id": self.chapter_id,
            "chapter_base": self.chapter_base,
            "units": [unit.to_wire() for unit in self.units],
        }

    def matches(self, other: SourceManifest) -> bool:
        """全部相等才算「来源没变」：拆段/合段会改变序列，因此也会被判不同。"""
        return (
            self.chapter_id == other.chapter_id
            and self.chapter_base == other.chapter_base
            and self.units == other.units
        )


def manifest_from(catalog: ManuscriptCatalog, chapter_id: str) -> SourceManifest:
    """按目录当前版本取来源清单；章不存在时由调用方先要求。"""
    units = tuple(
        SummarySource(unit_id=unit.artifact_id, revision_id=unit.revision_id)
        for unit in catalog.units_of(chapter_id)
    )
    return SourceManifest(
        chapter_id=chapter_id,
        chapter_base=catalog.chapter_base(chapter_id),
        units=units,
    )


def chapter_text(catalog: ManuscriptCatalog, chapter_id: str) -> str:
    """章的活动正文，按段落顺序拼；与 ``chapter_base`` 的口径一致。"""
    return "\n".join(unit.model().text for unit in catalog.units_of(chapter_id))


def render_summary_prompt(*, title: str, text: str, guidance: str = "") -> str:
    """确定性提示词：同一章同一版本，永远得到同一段要求。"""
    parts = [
        f"## 章节\n{title or '（未命名）'}",
        f"## 正文\n{text.strip()}",
    ]
    if guidance.strip():
        parts.append(f"## 作者附加要求\n{guidance.strip()}")
    parts.append(
        "## 输出要求\n"
        "- 用要点列出，不写开场白，不复述全文；\n"
        "- 覆盖：发生了什么、人物做了什么决定、谁知道了什么、出现或指向的线索、"
        "仍未解释的疑问、视角与时间位置；\n"
        "- 正文没写清楚的地方写「未交代」，不要补写；\n"
        f"- 总长度不超过 {MAX_SUMMARY_CHARS} 字。"
    )
    return "\n\n".join(parts)


def build_content(
    *,
    summary_id: str,
    manifest: SourceManifest,
    text: str,
    model: str,
    task_id: str,
    generated_at: str,
) -> dict[str, Any]:
    """摘要 Artifact 的完整内容。

    ``source_hash`` 用 ``chapter_base``：既有失效裁决只比较这个字段，因此新摘要
    与 V03-2.2 的判定口径天然一致，不需要两套「来源变没变」的标准。
    """
    return {
        "schema_version": 1,
        "summary_id": summary_id,
        "chapter_id": manifest.chapter_id,
        "source_hash": manifest.chapter_base,
        "status": SUMMARY_STATUS_FRESH,
        "reason": "",
        "text": text.strip(),
        "sources": manifest.to_wire(),
        "model": model,
        "task_id": task_id,
        "generated_at": generated_at,
    }


def decide(proposal: SourceManifest, current: SourceManifest) -> tuple[bool, str]:
    """落地前核对来源：一致才可采用，否则只报告事实、不写任何版本。"""
    if proposal.matches(current):
        return True, ""
    return (
        False,
        "生成期间章节正文已变化；本次结果不写入，避免覆盖新稿的摘要",
    )

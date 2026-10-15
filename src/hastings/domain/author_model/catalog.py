"""AuthorModelCatalog：把 Repository Snapshot 投影为作者模型目录（T14.5/T14.6）。

领域命令不直接访问存储：application handler 先取 Snapshot、构造
AuthorModelCatalog，再交给命令函数。目录提供按 ID 精确定位、按身份键
``(commit_id, unit_id)`` 判断某次编辑是否已有观察——重跑分析据此成为合法 no-op；
偏好按身份键 ``(类别, 作用域, 作用域目标)`` 过滤，供归纳判断「是否已有非撤销偏好」。

本模块是「读」抽象，不产生写入；写路径只经 ``AuthorModelChange`` 返回。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from hastings.domain.author_model import error_codes as ec
from hastings.domain.author_model import kinds
from hastings.domain.author_model.errors import violation
from hastings.domain.author_model.models import (
    AuthorExample,
    AuthorObservation,
    PreferenceHypothesis,
)
from hastings.domain.repository.models import Snapshot

__all__ = ["AuthorModelCatalog", "AuthorModelEntry", "decode"]

Decoder = Callable[[dict[str, Any]], Any]

_DECODERS: dict[str, Decoder] = {
    kinds.KIND_OBSERVATION: AuthorObservation.from_content,
    kinds.KIND_PREFERENCE: PreferenceHypothesis.from_content,
    kinds.KIND_EXAMPLE: AuthorExample.from_content,
}


def decode(kind: str, content: dict[str, Any]) -> Any:
    """按 kind 解码为领域模型；未注册 kind 返回原始 content。"""
    decoder = _DECODERS.get(kind)
    if decoder is None:
        return content
    return decoder(content)


@dataclass(frozen=True, slots=True)
class AuthorModelEntry:
    """Snapshot 中单个作者模型对象：身份 + 有效 Revision + 内容。"""

    artifact_id: str
    kind: str
    revision_id: str
    content: dict[str, Any]

    def model(self) -> Any:
        return decode(self.kind, self.content)


@dataclass(frozen=True, slots=True)
class AuthorModelCatalog:
    """某 Commit 的作者模型目录（只读）。commit_id 为 None 表示空目录。"""

    commit_id: str | None
    entries: tuple[AuthorModelEntry, ...]

    @classmethod
    def from_snapshot(cls, snapshot: Snapshot) -> AuthorModelCatalog:
        entries: list[AuthorModelEntry] = []
        for view in snapshot.artifacts.values():
            if view.revision is None:
                continue
            if not kinds.is_author_model_kind(view.artifact.kind):
                continue
            entries.append(
                AuthorModelEntry(
                    artifact_id=view.artifact.id.value,
                    kind=view.artifact.kind,
                    revision_id=view.revision.id.value,
                    content=dict(view.revision.content),
                )
            )
        entries.sort(key=lambda e: e.artifact_id)
        return cls(commit_id=snapshot.commit_id.value, entries=tuple(entries))

    # ------------------------------------------------------------------
    # 定位
    # ------------------------------------------------------------------
    def get(self, artifact_id: str) -> AuthorModelEntry | None:
        return next((e for e in self.entries if e.artifact_id == artifact_id), None)

    def by_kind(self, kind: str) -> tuple[AuthorModelEntry, ...]:
        return tuple(e for e in self.entries if e.kind == kind)

    def require(self, artifact_id: str, *, where: str) -> AuthorModelEntry:
        entry = self.get(artifact_id)
        if entry is None:
            raise violation(
                ec.AUTHOR_MODEL_UNKNOWN_ARTIFACT,
                where,
                f"作者模型对象 {artifact_id!r} 在当前快照中不存在",
                "改用快照内存在的 ID，或先登记该对象再引用",
                artifact_id=artifact_id,
            )
        return entry

    def observation_entry(self, observation_id: str) -> AuthorModelEntry | None:
        entry = self.get(observation_id)
        return entry if entry is not None and entry.kind == kinds.KIND_OBSERVATION else None

    # ------------------------------------------------------------------
    # 投影
    # ------------------------------------------------------------------
    def observations(self) -> tuple[AuthorObservation, ...]:
        """全部已登记观察（解码后，按 ArtifactId 排序，顺序稳定）。"""
        return tuple(entry.model() for entry in self.by_kind(kinds.KIND_OBSERVATION))

    def observation_for(self, commit_id: str, unit_id: str) -> AuthorObservation | None:
        """按身份键 ``(来源 Commit, 段落 ID)`` 找观察；无则 None（合法空结果）。"""
        key = (commit_id, unit_id)
        return next(
            (
                observation
                for observation in self.observations()
                if observation.identity_key() == key
            ),
            None,
        )

    # ------------------------------------------------------------------
    # 偏好（T14.6）
    # ------------------------------------------------------------------
    def preference_entry(self, preference_id: str) -> AuthorModelEntry | None:
        entry = self.get(preference_id)
        return entry if entry is not None and entry.kind == kinds.KIND_PREFERENCE else None

    def require_preference(self, preference_id: str, *, where: str) -> AuthorModelEntry:
        entry = self.preference_entry(preference_id)
        if entry is None:
            raise violation(
                ec.AUTHOR_MODEL_UNKNOWN_ARTIFACT,
                where,
                f"偏好 {preference_id!r} 在当前快照中不存在",
                "改用快照内存在的偏好 ID（先查询 preferences() 拿到 ID 列表）",
                preference_id=preference_id,
            )
        return entry

    def preferences(self) -> tuple[PreferenceHypothesis, ...]:
        """全部偏好（解码后，按 ArtifactId 排序，含 candidate 与 revoked 历史）。"""
        return tuple(entry.model() for entry in self.by_kind(kinds.KIND_PREFERENCE))

    def preferences_for(
        self,
        *,
        category: str | None = None,
        scope_kind: str | None = None,
        scope_target: str | None = None,
    ) -> tuple[PreferenceHypothesis, ...]:
        """按身份键 ``(类别, 作用域, 作用域目标)`` 过滤；None 表示该维度不过滤。"""
        result = self.preferences()
        if category is not None:
            result = tuple(p for p in result if p.category == category)
        if scope_kind is not None:
            result = tuple(p for p in result if p.scope_kind == scope_kind)
        if scope_target is not None:
            result = tuple(p for p in result if p.scope_target == scope_target)
        return result

    # ------------------------------------------------------------------
    # 样例（T14.7）
    # ------------------------------------------------------------------
    def example_entry(self, example_id: str) -> AuthorModelEntry | None:
        entry = self.get(example_id)
        return entry if entry is not None and entry.kind == kinds.KIND_EXAMPLE else None

    def require_example(self, example_id: str, *, where: str) -> AuthorModelEntry:
        entry = self.example_entry(example_id)
        if entry is None:
            raise violation(
                ec.AUTHOR_MODEL_UNKNOWN_ARTIFACT,
                where,
                f"样例 {example_id!r} 在当前快照中不存在",
                "改用快照内存在的样例 ID（先查询 examples() 拿到 ID 列表）",
                example_id=example_id,
            )
        return entry

    def examples(self) -> tuple[AuthorExample, ...]:
        """全部样例（解码后，按 ArtifactId 排序，含已删除的历史记录）。"""
        return tuple(entry.model() for entry in self.by_kind(kinds.KIND_EXAMPLE))

    def examples_for(self, *, unit_id: str, revision_id: str) -> tuple[AuthorExample, ...]:
        """按引用身份 ``(unit_id, revision_id)`` 找样例（含 removed，供命令判取代）。"""
        key = (unit_id, revision_id)
        return tuple(example for example in self.examples() if example.identity() == key)

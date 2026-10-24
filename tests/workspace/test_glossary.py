"""专有名词词典（0.6 V06-E3）。

契约：人物姓名与别名来自小传声明，地名、组织、物件由核对任务从原文登记；每条登记都要有
对得上的原文依据，源文件改过即失效。词典只帮助统一写法，不判断这个名字用得好不好。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hastings.workspace.assembly import Exclusion
from hastings.workspace.disclosure import ObservedEvidence
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.glossary import (
    GlossaryStore,
    GlossaryTerm,
    build_glossary,
    glossary_section,
    publish_terms,
)

_PERSON = """---
roles: [suspect]
aliases: [林医生]
---

# 林静

手术灯下站了整夜。
"""

_OTHER = """---
roles: [detective]
aliases: [林静]
---

# 佐藤警部

他合上了记事本。
"""


def _workspace(tmp_path: Path) -> Workspace:
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    workspace.write("正文/0001 雨夜.md", "第一章 雨夜\n\n雪山旅馆的灯亮着。", expected_digest=None)
    workspace.write("设定集/角色小传/林静.md", _PERSON, expected_digest=None)
    return workspace


def _term(workspace: Workspace, *, term: str = "雪山旅馆", chapter: int = 1) -> GlossaryTerm:
    chapter_file = workspace.read("正文/0001 雨夜.md")
    quote = "雪山旅馆"
    start = chapter_file.text.index(quote)
    return GlossaryTerm(
        term=term,
        kind="place",
        aliases=("旅馆",),
        first_chapter=chapter,
        source="text",
        evidence=ObservedEvidence(
            path=chapter_file.path,
            digest=chapter_file.digest,
            start=start,
            end=start + len(quote),
            quote=quote,
        ),
    )


def test_declared_names_become_person_terms(tmp_path: Path) -> None:
    state = build_glossary(_workspace(tmp_path))
    entity = next(term for term in state.terms if term.term == "林静")
    assert entity.kind == "person"
    assert entity.aliases == ("林医生",)
    assert entity.source == "declared"


def test_published_term_carries_its_evidence(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = GlossaryStore(tmp_path / "state")
    publish_terms(workspace, store, (_term(workspace),))

    state = build_glossary(workspace, store)
    place = next(term for term in state.terms if term.term == "雪山旅馆")
    assert place.source == "text"
    assert place.evidence is not None
    assert place.evidence.quote == "雪山旅馆"
    assert "地点：雪山旅馆（旅馆）" in glossary_section(state)


def test_stale_term_is_dropped_with_a_warning(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = GlossaryStore(tmp_path / "state")
    publish_terms(workspace, store, (_term(workspace),))

    chapter = workspace.read("正文/0001 雨夜.md")
    workspace.write(
        "正文/0001 雨夜.md",
        chapter.text + "\n风停了。",
        expected_digest=chapter.digest,
    )

    state = build_glossary(workspace, store)
    assert all(term.term != "雪山旅馆" for term in state.terms)
    assert any("已失效" in warning for warning in state.warnings)


def test_alias_pointing_at_another_person_is_a_conflict(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    workspace.write("设定集/角色小传/佐藤警部.md", _OTHER, expected_digest=None)
    state = build_glossary(workspace)
    # 一个角色的正名同时是另一个角色的别名：按「正名被占用」报出。
    assert [(conflict.term, conflict.kind) for conflict in state.conflicts] == [
        ("林静", "name_collision")
    ]


def test_term_introduced_later_is_not_listed_yet(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = GlossaryStore(tmp_path / "state")
    publish_terms(workspace, store, (_term(workspace, term="雪山旅馆", chapter=1),))
    state = build_glossary(workspace, store, as_of_chapter=0)
    # 尚未写到第一章时，该章的专名不进入白名单。
    assert "雪山旅馆" not in glossary_section(state)


def test_names_beyond_the_limit_keep_their_location(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    workspace.write("正文/0001 雨夜.md", "第一章 雨夜\n\n雪山旅馆后是山路。", expected_digest=None)
    chapter = workspace.read("正文/0001 雨夜.md")

    def term(name: str) -> GlossaryTerm:
        start = chapter.text.index(name)
        return GlossaryTerm(
            term=name,
            kind="place",
            aliases=(),
            first_chapter=1,
            source="text",
            evidence=ObservedEvidence(
                path=chapter.path,
                digest=chapter.digest,
                start=start,
                end=start + len(name),
                quote=name,
            ),
        )

    store = GlossaryStore(tmp_path / "state")
    publish_terms(workspace, store, (term("雪山旅馆"), term("山路")))
    excluded: list[Exclusion] = []

    state = build_glossary(workspace, store)
    section = glossary_section(state, limit=1, excluded=excluded)

    assert "还有" in section
    assert [(item.id, item.reason) for item in excluded] == [("glossary:山路", "limit")]
    assert excluded[0].location == "read_state domain=glossary"


def test_publish_requires_matching_evidence(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    store = GlossaryStore(tmp_path / "state")
    bad = _term(workspace)
    broken = GlossaryTerm(
        term=bad.term,
        kind=bad.kind,
        aliases=bad.aliases,
        first_chapter=bad.first_chapter,
        source="text",
        evidence=ObservedEvidence(
            path=bad.evidence.path,  # type: ignore[union-attr]
            digest=bad.evidence.digest,  # type: ignore[union-attr]
            start=bad.evidence.start,  # type: ignore[union-attr]
            end=bad.evidence.end,  # type: ignore[union-attr]
            quote="雪山",
        ),
    )
    with pytest.raises(WorkspaceError) as excinfo:
        publish_terms(workspace, store, (broken,))
    assert excinfo.value.code == "STATE_EVIDENCE_INVALID"
    assert store.entries() == ()

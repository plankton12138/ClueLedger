"""T13.10 应用层写入口测试：命令 → Guard → Commit Service → 查询读回。

覆盖四条主线：
- 空 head 首次提交、后续提交基于新 head 累积（head 每次重新读取）；
- 领域 ``StoryViolation`` 在边界转成 ``ViolationError``（VIOLATION，四要素透传）；
- 领域 no-op → ``NO_CHANGE``，不产生 Commit（合法空结果，四透镜③）；
- 3 章悬疑基线经命令建立并提交，经 StoryState / DisclosureTimeline / Canon 读回。

外加路由闸：``architecture_version`` 纯解析（缺失/非法回退 v1），V2 项目
``bible_upsert`` 被拒绝桩替换、读工具保留，V1 项目行为不变。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from hastings.application.errors import ResultCategory, ViolationError
from hastings.application.handlers.story_commands import (
    StoryCommand,
    StoryCommandHandler,
    StoryCommandResult,
    story_command,
)
from hastings.application.queries.story_queries import StoryQueries
from hastings.bible.store import StoryWorkspace
from hastings.domain.repository import error_codes as repo_ec
from hastings.domain.repository.ids import Actor, CommitId
from hastings.domain.repository.results import COMMITTED, NO_CHANGE
from hastings.domain.story import error_codes as ec
from hastings.domain.story import kinds, refs
from hastings.domain.story.canon import commands as canon
from hastings.domain.story.changes import DomainChange
from hastings.domain.story.mystery.models import TruthFact
from hastings.domain.story.mystery.rules import mystery_findings
from hastings.domain.story.narrative import commands as narrative
from hastings.domain.story.narrative.models import DisclosureSpec
from hastings.infrastructure.repository.memory import build_memory_repository
from hastings.interfaces.architecture_version import (
    ARCHITECTURE_VERSION_KEY,
    resolve_architecture_version,
)
from hastings.settings import workspace_architecture_version
from hastings.tools.bible_tools import create_bible_tools

ACTOR = Actor(kind="human", id="author_test")


def _command(func: Callable[..., DomainChange], **kwargs: Any) -> StoryCommand:
    """把关键字参数预绑到领域命令，得到带稳定身份与意图的 ``StoryCommand``。"""
    return story_command(func, **kwargs)


class _FakeClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)


class _FakeIds:
    def __init__(self) -> None:
        self._n = 0

    def new_id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}_{self._n:04d}"


@dataclass
class _Env:
    handler: StoryCommandHandler
    queries: StoryQueries
    service: Any  # MemoryRepository.service（读+写门面）


@pytest.fixture
def env() -> _Env:
    clock, ids = _FakeClock(), _FakeIds()
    repo = build_memory_repository(clock, ids)
    handler = StoryCommandHandler(repo.service, repo.service, ids, ACTOR)
    return _Env(handler=handler, queries=StoryQueries(repo.service), service=repo.service)


class _Runner:
    """按序执行命令的测试驱动：自动生成唯一幂等键，并暴露新建对象 ID。"""

    def __init__(self, env: _Env, project: Any) -> None:
        self._env = env
        self._project = project
        self._n = 0

    def __call__(self, command: StoryCommand, summary: str = "提交") -> StoryCommandResult:
        self._n += 1
        return self._env.handler.execute(
            command, project_id=self._project.id, idempotency_key=f"k{self._n}", summary=summary
        )

    @staticmethod
    def created_id(result: StoryCommandResult) -> str:
        """命令新建对象的 ArtifactId（单对象命令的第一个变更）。"""
        return result.domain_change.artifact_changes[0].artifact_id.value


def _head(env: _Env, project: Any) -> CommitId:
    branch = env.service.get_branch(project.id, "main")
    assert branch is not None and branch.head_commit_id is not None
    return branch.head_commit_id


def _character(name: str, role: str) -> StoryCommand:
    return _command(canon.create_character, name=name, role=role)


# ---------------------------------------------------------------------------
# 写入口机制
# ---------------------------------------------------------------------------
def test_first_commit_on_empty_head(env: _Env) -> None:
    project = env.handler.create_project("冬馆")
    branch = env.service.get_branch(project.id, "main")
    assert branch is not None and branch.head_commit_id is None  # 首提交前 head 为空

    run = _Runner(env, project)
    result = run(_character("宫泽健一", role="protagonist"))

    assert result.status == COMMITTED and result.is_committed
    assert result.new_head is not None
    catalog = env.queries.catalog(project.id, _head(env, project))
    assert [e.kind for e in catalog.entries] == [kinds.KIND_CHARACTER]


def test_second_commit_sees_first(env: _Env) -> None:
    project = env.handler.create_project("冬馆")
    run = _Runner(env, project)
    run(_character("宫泽健一", role="protagonist"))
    first_head = _head(env, project)
    run(_character("雾岛彻", role="antagonist"))

    assert _head(env, project) != first_head
    catalog = env.queries.catalog(project.id, _head(env, project))
    assert len(catalog.by_kind(kinds.KIND_CHARACTER)) == 2


def test_domain_violation_becomes_application_error(env: _Env) -> None:
    project = env.handler.create_project("冬馆")
    run = _Runner(env, project)

    with pytest.raises(ViolationError) as exc:
        run(_character("幽灵", role="villain"))

    error = exc.value
    assert error.category == ResultCategory.VIOLATION
    assert error.code == ec.STORY_INVALID_COMMAND
    assert error.where and error.why and error.fix  # 三要素齐全
    assert error.details["role"] == "villain"
    # 违规不产生任何提交：head 仍为空
    after = env.service.get_branch(project.id, "main")
    assert after is not None and after.head_commit_id is None


def test_domain_no_op_does_not_commit(env: _Env) -> None:
    project = env.handler.create_project("冬馆")
    run = _Runner(env, project)
    created = run(_character("宫泽健一", role="protagonist"))
    character_id = _Runner.created_id(created)
    head_after_create = _head(env, project)

    noop = run(_command(canon.revise_character, character_id=character_id, name="宫泽健一"))

    assert noop.status == NO_CHANGE and noop.commit is None
    assert "未变化" in noop.summary
    assert _head(env, project) == head_after_create  # 未产生新 Commit


def test_idempotent_retry_replays_without_reexecuting(env: _Env) -> None:
    """评审 P1：同键重试返回首次结果，不重跑命令、不生成新 Artifact ID。"""
    project = env.handler.create_project("冬馆")
    command = _character("宫泽健一", role="protagonist")

    first = env.handler.execute(
        command, project_id=project.id, idempotency_key="same", summary="建主角"
    )
    retry = env.handler.execute(
        command, project_id=project.id, idempotency_key="same", summary="建主角"
    )

    assert first.status == COMMITTED
    assert retry is first  # 同一结果对象：确认未重新执行命令
    assert retry.commit_id == first.commit_id
    assert retry.new_head == first.new_head
    catalog = env.queries.catalog(project.id, _head(env, project))
    assert len(catalog.by_kind(kinds.KIND_CHARACTER)) == 1  # 未产生第二个 Artifact


def test_idempotent_retry_replays_no_change(env: _Env) -> None:
    """合法空结果同样纳入重放：重试返回同一次 no_change，不重新读 head。"""
    project = env.handler.create_project("冬馆")
    created = env.handler.execute(
        _character("宫泽健一", role="protagonist"),
        project_id=project.id,
        idempotency_key="k1",
        summary="建主角",
    )
    character_id = _Runner.created_id(created)
    noop = _command(canon.revise_character, character_id=character_id, name="宫泽健一")

    first = env.handler.execute(noop, project_id=project.id, idempotency_key="k2", summary="改名")
    retry = env.handler.execute(noop, project_id=project.id, idempotency_key="k2", summary="改名")

    assert first.status == NO_CHANGE
    assert retry is first


def test_same_idempotency_key_different_intent_conflicts(env: _Env) -> None:
    """同键不同意图必须显式冲突，不静默返回旧结果（与 Commit Service 同哲学）。"""
    project = env.handler.create_project("冬馆")
    env.handler.execute(
        _character("宫泽健一", role="protagonist"),
        project_id=project.id,
        idempotency_key="dup",
        summary="建主角",
    )

    with pytest.raises(ViolationError) as exc:
        env.handler.execute(
            _character("雾岛彻", role="antagonist"),
            project_id=project.id,
            idempotency_key="dup",
            summary="建反派",
        )

    assert exc.value.category == ResultCategory.VIOLATION
    assert exc.value.code == repo_ec.REPO_IDEMPOTENCY_CONFLICT
    assert exc.value.fix  # 给出修正指引


# ---------------------------------------------------------------------------
# 3 章悬疑基线：命令建立 → 提交 → 读回
# ---------------------------------------------------------------------------
def _build_baseline(env: _Env, project: Any) -> dict[str, Any]:
    """经命令建立 3 章悬疑基线：1 真凶、1 协助者、3 必要线索、2 误导。"""
    run = _Runner(env, project)
    culprit = _Runner.created_id(run(_character("雾岛彻", role="antagonist")))
    accomplice = _Runner.created_id(run(_character("绫辻绫", role="accomplice")))
    run(_character("宫泽健一", role="protagonist"))

    series = _Runner.created_id(
        run(_command(narrative.create_node, kind=kinds.KIND_SERIES, title="冬馆"))
    )
    book = _Runner.created_id(
        run(_command(narrative.create_node, kind=kinds.KIND_BOOK, parent_id=series, title="上卷"))
    )
    arc = _Runner.created_id(
        run(_command(narrative.create_node, kind=kinds.KIND_ARC, parent_id=book, title="第一幕"))
    )
    chapters = [
        _Runner.created_id(
            run(
                _command(
                    narrative.create_node,
                    kind=kinds.KIND_CHAPTER,
                    parent_id=arc,
                    title=f"第{i}章",
                    order_key=f"{i:03d}",
                )
            )
        )
        for i in (1, 2, 3)
    ]
    beats = [
        _Runner.created_id(
            run(_command(narrative.create_beat, parent_id=ch, title=f"节拍{i}", order_key="010"))
        )
        for i, ch in enumerate(chapters, start=1)
    ]
    truth = _Runner.created_id(
        run(
            _command(
                canon.define_mystery_truth,
                culprit_id=culprit,
                accomplice_ids=(accomplice,),
            )
        )
    )
    for i in range(3):
        run(
            _command(
                canon.plan_clue,
                statement=f"线索{i + 1}",
                points_to=(truth,),
                necessity=True,
                disclosure_plan=(beats[0],),
            )
        )
    for i in range(2):
        run(
            _command(
                canon.plan_red_herring,
                false_explanation=f"误导{i + 1}",
                basis="管家确有隐瞒，其行踪可被误读",
            )
        )
    return {"culprit": culprit, "beats": beats, "truth": truth}


def test_baseline_built_by_commands_and_read_back(env: _Env) -> None:
    project = env.handler.create_project("冬馆")
    _build_baseline(env, project)
    head = _head(env, project)

    canon_view = env.queries.canon_snapshot(project.id, head)
    tree = env.queries.narrative_tree(project.id, head)
    state = env.queries.story_state(project.id, head)
    timeline = env.queries.disclosure_timeline(project.id, head)

    assert canon_view.commit_id == head.value
    fact_kinds = [e.kind for e in canon_view.facts]
    assert fact_kinds.count(kinds.KIND_CHARACTER) == 3
    assert fact_kinds.count(kinds.KIND_MYSTERY_TRUTH) == 1
    assert fact_kinds.count(kinds.KIND_CLUE) == 3
    assert fact_kinds.count(kinds.KIND_RED_HERRING) == 2

    assert tree.commit_id == head.value
    assert len(tree.roots) == 1  # 单 Series 根
    catalog = env.queries.catalog(project.id, head)
    structure_ids = {e.artifact_id for e in (*catalog.nodes(), *catalog.beats())}
    assert set(tree.order) == structure_ids  # 顺序覆盖全部结构节点与节拍
    assert len(tree.order) == 9  # 6 节点（系列/卷/幕/3 章）+ 3 节拍

    assert state.commit_id == head.value and state.at_beat_id is None
    assert timeline.commit_id == head.value

    # 确定性规则：三条必要线索都在首个节拍披露，公平性截止 0.5 号令下全部通过
    assert mystery_findings(catalog, fairness_cutoff=0.5) == ()


# ---------------------------------------------------------------------------
# 三层悬疑事实：真凶先行知情 → 定位明确的确定性错误
# ---------------------------------------------------------------------------
def _seed_truth_fact_impl(
    catalog: Any, new_id: Callable[[str], str], *, statement: str
) -> DomainChange:
    """测试内定义一个 TruthFact 命令（首批命令尚未含它，见构建笔记留白）。

    它与产品命令走同一条路径：handler → CommitRequest → Commit Service，
    因此仍是「所有写命令经 Application Handler 和 Commit Service」。
    """
    artifact_id = refs.new_artifact_id(catalog, new_id, kinds.KIND_TRUTH_FACT, cmd="SeedTruthFact")
    return DomainChange(
        artifact_changes=(
            refs.create_change(
                artifact_id,
                kinds.KIND_TRUTH_FACT,
                TruthFact(statement=statement).to_content(),
            ),
        ),
        summary=f"登记事实 {statement!r}",
    )


def _seed_truth_fact(statement: str) -> StoryCommand:
    return story_command(_seed_truth_fact_impl, statement=statement)


def test_early_knowledge_yields_located_finding(env: _Env) -> None:
    project = env.handler.create_project("冬馆")
    run = _Runner(env, project)
    culprit = _Runner.created_id(run(_character("雾岛彻", role="antagonist")))
    series = _Runner.created_id(
        run(_command(narrative.create_node, kind=kinds.KIND_SERIES, title="冬馆"))
    )
    book = _Runner.created_id(
        run(_command(narrative.create_node, kind=kinds.KIND_BOOK, parent_id=series, title="上卷"))
    )
    arc = _Runner.created_id(
        run(_command(narrative.create_node, kind=kinds.KIND_ARC, parent_id=book, title="第一幕"))
    )
    chapter = _Runner.created_id(
        run(_command(narrative.create_node, kind=kinds.KIND_CHAPTER, parent_id=arc, title="第一章"))
    )
    truth_fact = _Runner.created_id(run(_seed_truth_fact("书房门从内反锁")))
    early_beat = _Runner.created_id(
        run(_command(narrative.create_beat, parent_id=chapter, title="入场", order_key="010"))
    )
    reveal_beat = _Runner.created_id(
        run(
            _command(
                narrative.create_beat,
                parent_id=chapter,
                title="揭晓",
                order_key="020",
                reader_disclosures=(DisclosureSpec(fact_id=truth_fact),),
            )
        )
    )
    # 真凶在揭示之前就以「已知」为前提 → 故意提前知情
    run(
        _command(
            canon.record_knowledge_change,
            character_id=culprit,
            fact_id=truth_fact,
            mode="knows",
            at_beat_id=early_beat,
        )
    )
    head = _head(env, project)

    state = env.queries.story_state(project.id, head)
    timeline = env.queries.disclosure_timeline(project.id, head)
    catalog = env.queries.catalog(project.id, head)

    assert [(k.character_id, k.fact_id, k.mode) for k in state.knowledge] == [
        (culprit, truth_fact, "knows")
    ]
    assert {e.fact_id for e in timeline.events} == {truth_fact}
    assert any(e.at_beat_id == reveal_beat for e in timeline.events)

    findings = mystery_findings(catalog)
    assert [f.code for f in findings] == [ec.STORY_INVALID_KNOWLEDGE_ORDER]
    assert findings[0].details["know_beat"] == early_beat
    assert findings[0].details["reveal_beat"] == reveal_beat
    assert early_beat in findings[0].why  # 定位精确到节拍（why 与 details 双通道）


def test_acceptance_loop_misbelief_then_corrected_early_knowledge(env: _Env) -> None:
    """计划 §7 闭环：误解合法 → 提前知情定位报错 → 移动揭示节拍修正 → 规则通过。

    1. 角色误解（misbelieves）永不被拦——它是合法叙事手段（T13.5）；
    2. 真凶在揭示之前自称已知 → 定位到节拍的 STORY_INVALID_KNOWLEDGE_ORDER；
    3. 用既有 MoveBeat 把揭示节拍前移到知情节拍之前（节拍 ID 不变）后重算，
       确定性规则不再报错——修正走的是同一套命令与提交路径。
    """
    project = env.handler.create_project("冬馆")
    run = _Runner(env, project)
    culprit = _Runner.created_id(run(_character("雾岛彻", role="antagonist")))
    witness = _Runner.created_id(run(_character("宫泽健一", role="protagonist")))
    series = _Runner.created_id(
        run(_command(narrative.create_node, kind=kinds.KIND_SERIES, title="冬馆"))
    )
    book = _Runner.created_id(
        run(_command(narrative.create_node, kind=kinds.KIND_BOOK, parent_id=series, title="上卷"))
    )
    arc = _Runner.created_id(
        run(_command(narrative.create_node, kind=kinds.KIND_ARC, parent_id=book, title="第一幕"))
    )
    chapter = _Runner.created_id(
        run(_command(narrative.create_node, kind=kinds.KIND_CHAPTER, parent_id=arc, title="第一章"))
    )
    truth_fact = _Runner.created_id(run(_seed_truth_fact("书房门从内反锁")))
    know_beat = _Runner.created_id(
        run(_command(narrative.create_beat, parent_id=chapter, title="现场", order_key="020"))
    )
    reveal_beat = _Runner.created_id(
        run(
            _command(
                narrative.create_beat,
                parent_id=chapter,
                title="揭晓",
                order_key="030",
                reader_disclosures=(DisclosureSpec(fact_id=truth_fact),),
            )
        )
    )
    # 合法误解：见证者信了错误解释，规则不应拦
    run(
        _command(
            canon.record_knowledge_change,
            character_id=witness,
            fact_id=truth_fact,
            mode="misbelieves",
            at_beat_id=know_beat,
        )
    )
    # 真凶提前知情：在揭示（030）之前于 020 已「已知」→ 定位报错
    run(
        _command(
            canon.record_knowledge_change,
            character_id=culprit,
            fact_id=truth_fact,
            mode="knows",
            at_beat_id=know_beat,
        )
    )

    head_before = _head(env, project)
    before = env.queries.catalog(project.id, head_before)
    findings = mystery_findings(before)
    assert [f.code for f in findings] == [ec.STORY_INVALID_KNOWLEDGE_ORDER]
    assert findings[0].details["know_beat"] == know_beat
    assert findings[0].details["reveal_beat"] == reveal_beat
    # 误解从未被误报：只有提前知情一条 finding
    assert len(findings) == 1

    # 修正：把揭示节拍前移到知情节拍之前（MoveBeat 保 ID、重排状态折叠顺序）
    run(_command(narrative.move_beat, beat_id=reveal_beat, order_key="010"))

    head_after = _head(env, project)
    assert head_after != head_before  # 修正经 Commit Service 落为新 Commit
    after = env.queries.catalog(project.id, head_after)
    assert mystery_findings(after) == ()
    # 修正不动节拍身份：揭晓节拍的 Artifact ID 未变
    assert {e.artifact_id for e in after.beats()} >= {know_beat, reveal_beat}


# ---------------------------------------------------------------------------
# architecture_version 路由闸
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        (None, "v1"),
        ({}, "v1"),
        ({ARCHITECTURE_VERSION_KEY: "v1"}, "v1"),
        ({ARCHITECTURE_VERSION_KEY: "v2"}, "v2"),
        ({ARCHITECTURE_VERSION_KEY: "V2"}, "v1"),  # 非法取值回退 v1
        ({ARCHITECTURE_VERSION_KEY: 2}, "v1"),
        ({"other": "v2"}, "v1"),
    ],
)
def test_resolve_architecture_version(metadata: dict[str, object] | None, expected: str) -> None:
    assert resolve_architecture_version(metadata) == expected


@pytest.mark.anyio
async def test_bible_tools_are_readonly_after_v1_retirement(tmp_path: Path) -> None:
    """V1 弃用后：bible 工具只剩 bible_query，写工具不再存在，读通道保留。"""
    workspace = StoryWorkspace(root=tmp_path, slug="ro-case")
    workspace.ensure_dirs()
    tools = {t.name: t for t in create_bible_tools(workspace)}
    assert set(tools) == {"bible_query"}

    # 读工具保留：V1 只读工作区核对设定仍走它
    queried = await tools["bible_query"].execute("call2", {"entity_type": "character"})
    assert "空" in queried.text


def test_workspace_architecture_version_reads_settings_metadata(tmp_path: Path) -> None:
    """真实装配点读的是 settings.yaml 元数据：v2 → v2，缺文件/非法 → v1。

    CLI 与 Web 会话共用本函数解析版本（ADR §2.2「项目解析这一处」），
    不再是只在测试里手工传 v2（评审 P1）。
    """
    workspace = StoryWorkspace(root=tmp_path, slug="meta-case")
    workspace.ensure_dirs()
    assert workspace_architecture_version(workspace) == "v1"  # 缺 settings.yaml

    workspace.settings_path.write_text("architecture_version: v2\n", encoding="utf-8")
    assert workspace_architecture_version(workspace) == "v2"

    workspace.settings_path.write_text("architecture_version: V2\n", encoding="utf-8")
    assert workspace_architecture_version(workspace) == "v1"  # 非法取值回退

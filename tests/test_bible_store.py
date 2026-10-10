"""Bible 存储层测试：YAML 往返、非法名拒绝、空库行为。"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from hastings.bible.models import (
    AlibiClaim,
    Character,
    Clue,
    Foreshadow,
    Method,
    TimelineEntry,
)
from hastings.bible.store import (
    InvalidEntityNameError,
    LedgerFormatError,
    StoryWorkspace,
    list_characters,
    load_character,
    load_ledger,
    load_story_doc,
    save_character,
    save_ledger,
    save_story_doc,
)
from hastings.bible.story_state import StageTransition, StoryDoc


def _ws(tmp_path: Path) -> StoryWorkspace:
    return StoryWorkspace(root=tmp_path / "stories", slug="demo")


def test_character_requires_name() -> None:
    with pytest.raises(ValidationError):
        Character(name="")


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    character = Character(
        name="波洛",
        identity="退休侦探",
        motivation="维护正义",
        secret="过去的一桩悬案",
        relationships=["信任黑斯廷斯", "怀疑管家"],
        notes="小小的灰色细胞",
    )
    path = save_character(ws, character)

    assert path == ws.characters_dir / "波洛.yaml"
    assert path.exists()
    assert load_character(ws, "波洛") == character


def test_yaml_keeps_unicode(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    path = save_character(ws, Character(name="波洛", identity="退休侦探"))

    assert "退休侦探" in path.read_text(encoding="utf-8")


def test_load_missing_returns_none(tmp_path: Path) -> None:
    assert load_character(_ws(tmp_path), "不存在") is None


def test_list_empty_when_no_dir(tmp_path: Path) -> None:
    assert list_characters(_ws(tmp_path)) == []


def test_list_sorted_by_filename(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    save_character(ws, Character(name="b-嫌疑人"))
    save_character(ws, Character(name="a-侦探"))

    assert [c.name for c in list_characters(ws)] == ["a-侦探", "b-嫌疑人"]


@pytest.mark.parametrize(
    "bad_name",
    ["../evil", "/abs", "a/b", "a\\b", " leading", "dot.name", "semi;colon", "trailing."],
)
def test_invalid_names_rejected(tmp_path: Path, bad_name: str) -> None:
    ws = _ws(tmp_path)
    with pytest.raises(InvalidEntityNameError):
        save_character(ws, Character(name=bad_name))
    with pytest.raises(InvalidEntityNameError):
        load_character(ws, bad_name)


@pytest.mark.parametrize("reserved", ["CON", "nul", "Com1", "aux", "LPT2"])
def test_windows_reserved_names_rejected(tmp_path: Path, reserved: str) -> None:
    """R3 #3：保留设备名在任何大小写下都拒绝，避免写盘 OSError 让模型盲目重试。"""
    ws = _ws(tmp_path)
    with pytest.raises(InvalidEntityNameError, match="(?i)reserved"):
        save_character(ws, Character(name=reserved))


@pytest.mark.parametrize("bad_slug", ["../escape", "a/b", "a\\b", "dot.name", "..", ""])
def test_invalid_slug_rejected(tmp_path: Path, bad_slug: str) -> None:
    """R3 #2：slug 是目录名来源，与实体名同等穿越防护。"""
    with pytest.raises(InvalidEntityNameError):
        StoryWorkspace(root=tmp_path, slug=bad_slug)


def test_valid_slug_accepted(tmp_path: Path) -> None:
    ws = StoryWorkspace(root=tmp_path, slug="orient-express")
    assert ws.story_dir == tmp_path / "orient-express"


def test_character_arc_defaults_to_flat() -> None:
    """P1 旧工作区 YAML 无 arc 字段照常加载（验收标准 6，模型侧保证）。"""
    assert Character(name="波洛").arc == "flat"


def test_ledger_models_default_to_initial_status() -> None:
    """各账本零参数新建即初始状态，与 state_machine.INITIAL_STATUS 对齐（D1）。"""
    assert Clue(name="染血的信").status == "planned"
    assert Foreshadow(name="停摆的挂钟").status == "planned"
    assert Method(name="密室诡计").status == "drafted"


def test_method_carries_alibi_claims_and_history() -> None:
    """D3 方案 A：alibi_claims 结构化字段；history 记锁定/解锁（T4 落点）。"""
    method = Method(
        name="密室诡计",
        alibi_claims=[
            AlibiClaim(person="管家", day=3, t_start="21:00", t_end="23:00", claim="整晚下棋")
        ],
        history=[],
    )

    assert method.alibi_claims[0].person == "管家"
    assert method.history == []


def test_timeline_entry_structure() -> None:
    """D2：结构化整数天 + 可选时钟；participants 缺省为空（D8 校验在工具层）。"""
    entry = TimelineEntry(name="发现尸体", day=1, t_start="07:30", t_end="08:00", chapter="ch01")

    assert entry.t_start == "07:30"
    assert entry.participants == []
    assert entry.note == ""


# --- P2 T2：单文件账本存储（D7） ---


def test_ledger_paths_follow_d7_layout(tmp_path: Path) -> None:
    """四账本单文件路径：clues/foreshadows/method（单数）/timeline。"""
    ws = _ws(tmp_path)

    assert ws.clues_path == ws.bible_dir / "clues.yaml"
    assert ws.foreshadows_path == ws.bible_dir / "foreshadows.yaml"
    assert ws.method_path == ws.bible_dir / "method.yaml"
    assert ws.timeline_path == ws.bible_dir / "timeline.yaml"


def test_clue_ledger_roundtrip(tmp_path: Path) -> None:
    ws = _ws(tmp_path)
    clues = [
        Clue(name="染血的信", content="信纸边缘有干涸血迹", chapter="ch02", points_to="管家"),
        Clue(name="停摆的挂钟", content="指针停在 21:47", is_red_herring=True),
    ]

    path = save_ledger(ws.clues_path, clues)

    assert path == ws.clues_path
    assert load_ledger(ws.clues_path, Clue) == clues


def test_method_ledger_roundtrip_keeps_nested_models(tmp_path: Path) -> None:
    """嵌套模型（alibi_claims/history）往返一致，mypy 收窄为 list[Method]。"""
    ws = _ws(tmp_path)
    method = Method(
        name="密室诡计",
        description="反锁密室实为延时机关",
        time_window="第3天晚 21:00-23:00",
        requirements=["窗户插销可从外操纵"],
        alibi_claims=[
            AlibiClaim(person="管家", day=3, t_start="21:00", t_end="23:00", claim="整晚下棋")
        ],
    )

    save_ledger(ws.method_path, [method])
    loaded = load_ledger(ws.method_path, Method)

    assert loaded == [method]
    assert loaded[0].alibi_claims[0].claim == "整晚下棋"


def test_ledger_missing_file_returns_empty(tmp_path: Path) -> None:
    """缺文件视为空账本——P1 旧工作区兼容的存储侧保证（验收标准 6）。"""
    ws = _ws(tmp_path)

    assert load_ledger(ws.clues_path, Clue) == []
    assert load_ledger(ws.foreshadows_path, Foreshadow) == []
    assert load_ledger(ws.method_path, Method) == []
    assert load_ledger(ws.timeline_path, TimelineEntry) == []


def test_ledger_empty_file_returns_empty(tmp_path: Path) -> None:
    """空文件（yaml 解析为 None）视为空账本，不让模型收到格式报错。"""
    ws = _ws(tmp_path)
    ws.clues_path.parent.mkdir(parents=True)
    ws.clues_path.write_text("", encoding="utf-8")

    assert load_ledger(ws.clues_path, Clue) == []


def test_ledger_non_list_root_rejected(tmp_path: Path) -> None:
    """根节点非列表 = 数据损坏，显式报错（泄压阀：环境故障≠空结果）。"""
    ws = _ws(tmp_path)
    ws.clues_path.parent.mkdir(parents=True)
    ws.clues_path.write_text("name: 染血的信\n", encoding="utf-8")

    with pytest.raises(LedgerFormatError, match="列表"):
        load_ledger(ws.clues_path, Clue)


def test_ledger_save_creates_bible_dir(tmp_path: Path) -> None:
    """账本写盘自建 bible/ 目录，不要求先 ensure_dirs。"""
    ws = _ws(tmp_path)

    save_ledger(ws.timeline_path, [TimelineEntry(name="发现尸体", day=1)])

    assert ws.timeline_path.exists()
    assert load_ledger(ws.timeline_path, TimelineEntry)[0].name == "发现尸体"


def test_p1_workspace_with_only_characters_dir_opens(tmp_path: Path) -> None:
    """P1 存量工作区（仅 characters/）照常打开：角色可读、四账本为空。"""
    ws = _ws(tmp_path)
    save_character(ws, Character(name="波洛", identity="退休侦探"))

    assert [c.name for c in list_characters(ws)] == ["波洛"]
    assert load_ledger(ws.clues_path, Clue) == []
    assert load_ledger(ws.method_path, Method) == []
    assert load_ledger(ws.foreshadows_path, Foreshadow) == []
    assert load_ledger(ws.timeline_path, TimelineEntry) == []


# --- P8 T1b：story.yaml 生命周期存储（计划 D1） ---


def test_story_doc_load_missing_defaults_to_intake(tmp_path: Path) -> None:
    """缺 story.yaml（P7 及更早旧工作区）按 intake 兜底，不强制迁移。"""
    ws = _ws(tmp_path)
    doc = load_story_doc(ws)

    assert doc.slug == "demo"
    assert doc.stage == "intake"
    assert doc.stage_history == []


def test_story_doc_roundtrip_keeps_history(tmp_path: Path) -> None:
    """保存/读取往返一致；YAML 键用 from/to（计划 D1 约定），at 留痕。"""
    ws = _ws(tmp_path)
    doc = StoryDoc(
        slug="demo",
        stage="bible_drafting",
        stage_history=[
            StageTransition.model_validate(
                {
                    "from": "intake",
                    "to": "bible_drafting",
                    "reason": "作者交付完整构想",
                    "at": "2026-10-10T22:00:00",
                }
            )
        ],
    )

    path = save_story_doc(ws, doc)

    assert path == ws.story_path
    assert "from:" in path.read_text(encoding="utf-8")  # YAML 键是 from/to
    assert load_story_doc(ws) == doc


def test_story_doc_invalid_stage_rejected(tmp_path: Path) -> None:
    """stage 只接受六阶段字面量；手改坏值显式报错（泄压阀：数据故障≠静默兜底）。"""
    ws = _ws(tmp_path)
    ws.story_dir.mkdir(parents=True)
    ws.story_path.write_text("slug: demo\nstage: archived\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        load_story_doc(ws)


def test_story_doc_save_creates_story_dir(tmp_path: Path) -> None:
    ws = _ws(tmp_path)

    save_story_doc(ws, StoryDoc(slug="demo"))

    assert ws.story_path.exists()
    assert load_story_doc(ws).stage == "intake"

"""CLI 装配测试（P8 T5 补齐）：build_harness 与 Web 端同一状态注入行为。

build_harness 是 CLI 冒烟入口的装配点——它必须与 HastingsSession.from_workspace
对齐：system prompt 都带「当前工作区状态」节，否则 CLI 走查观察到的模型行为
（协议遵循、阶段感知）与 Web 端不一致，冒烟结论不可信。
"""

from pathlib import Path

from hastings.bible.store import StoryWorkspace, save_story_doc
from hastings.bible.story_state import StoryDoc
from hastings.cli import build_harness
from hastings.prompts import STATUS_HEADING
from tau_ai import FakeProvider


def test_build_harness_injects_workspace_status(tmp_path: Path) -> None:
    """CLI 装配的 system prompt 与 Web 端对齐：写作中工作区注入状态节。"""
    workspace = StoryWorkspace(root=tmp_path, slug="demo")
    save_story_doc(workspace, StoryDoc(slug="demo", stage="writing"))

    harness = build_harness(workspace, FakeProvider([]), "fake")

    assert STATUS_HEADING in harness.config.system
    assert "阶段：writing" in harness.config.system

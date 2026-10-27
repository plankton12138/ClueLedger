"""0.9 U1：请求输入的共用装配。

估算与执行必须从同一处取 system 与工具定义。两边各装配一次时，差别往往只在某个
分区的顺序或某一版资料上，靠肉眼对比发现不了，却会让占用数字与实际请求脱节。

这里不重测装配内容本身（那由上下游用例覆盖），只钉两条：harness 确实走这个入口；
构造过程不产生作品改动。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hastings.workspace import agent as agent_module
from hastings.workspace.agent import (
    RunContext,
    build_harness,
    prepare_request_inputs,
)
from hastings.workspace.files import Workspace
from hastings.workspace.git import GitRepo
from hastings.workspace.service import WriteSession


def _run(tmp_path: Path) -> tuple[Workspace, RunContext]:
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    GitRepo(workspace.root).init()
    session = WriteSession(workspace, instruction="写第一章")
    run = RunContext(workspace, session, author_messages=((1, "写第一章，改写雨夜这一章。"),))
    return workspace, run


def test_harness_uses_the_shared_request_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """harness 不再自己装配：改一处就同时改变执行与估算看到的输入。"""
    workspace, run = _run(tmp_path)
    calls: list[str] = []
    original = agent_module.prepare_request_inputs

    def spy(**kwargs: object) -> agent_module.RequestInputs:
        calls.append(str(kwargs.get("mode")))
        return original(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(agent_module, "prepare_request_inputs", spy)

    build_harness(provider=object(), model="m1", workspace=workspace, run=run)  # type: ignore[arg-type]

    assert calls == ["write"]


def test_prepare_request_inputs_does_not_touch_the_workspace(tmp_path: Path) -> None:
    """只读估算不能带来任何作品改动。"""
    workspace, run = _run(tmp_path)
    before = sorted(
        str(path.relative_to(workspace.root))
        for path in workspace.root.rglob("*")
        if path.is_file()
    )

    inputs = prepare_request_inputs(workspace=workspace, run=run)

    after = sorted(
        str(path.relative_to(workspace.root))
        for path in workspace.root.rglob("*")
        if path.is_file()
    )
    assert inputs.system
    assert inputs.tools
    assert after == before


def test_request_inputs_carry_both_parts_for_estimation(tmp_path: Path) -> None:
    """工具定义与系统提示一起返回，估算才可能算全。"""
    workspace, run = _run(tmp_path)

    inputs = prepare_request_inputs(workspace=workspace, run=run)

    names = [tool.name for tool in inputs.tools]
    assert "read_file" in names
    assert "系统" in inputs.system or "写作" in inputs.system

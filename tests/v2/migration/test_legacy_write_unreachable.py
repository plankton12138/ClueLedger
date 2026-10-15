"""旧写路径不可达证明（plan §5 T17.6；V1 弃用后升级为整体下线证明）。

历史演进：T17.6 时写工具对 V2 项目替换为拒绝桩、REST 旧写口靠 409 架构门；
V1 直接弃用后（项目未上线，无存量用户），写工具与 REST 旧写口**整体删除**。
本测试枚举三类可达面：

1. **REST 路由**：V2 应用的整张路由表不含旧端点（bible/upsert/stage/reconcile）；
2. **工具集**：写工具（bible_upsert / write_chapter / method_lock /
   method_unlock / story_stage）不再存在于任何工具集，只读工具保持可用；
3. **模块依赖**：V2 分层（domain/application）不 import 旧存储与旧运行时
   （bible.store / events_store / server.reconcile / session 写锁 / sqlite3 / yaml）；
   interfaces 除 importers 的**只读**读取外也不触达旧写路径。

三条都用「枚举实际可达面再断言」的方式，而不是逐点打勾，避免新增入口时漏测。
**真实服务装配**（``create_app`` 同时挂载 V1 REST 与 V2 REST）下的端点下线证明由
``tests/test_v2_write_gate.py`` 验证——本文件只覆盖独立 V2 应用与工具/模块面。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from hastings.bible.store import StoryWorkspace
from hastings.cli import create_writing_tools
from hastings.server.v2 import V2Runtime, build_v2_app

from .conftest import FIXTURE, SLUG

SRC = Path(__file__).resolve().parents[3] / "src" / "hastings"

# 旧写入口 / 旧运行时标识：出现在 V2 可达面上即为漂移
_LEGACY_ROUTE_TOKENS = ("bible", "upsert", "story_stage", "stage", "reconcile")
_LEGACY_WRITE_TOOLS = frozenset(
    ("bible_upsert", "write_chapter", "method_lock", "method_unlock", "story_stage")
)
_READ_TOOLS = frozenset(("bible_query", "read_chapter", "list_chapters", "timeline_check"))

_FORBIDDEN = {
    "domain": {
        "hastings.bible.store",
        "hastings.events_store",
        "hastings.server.reconcile",
        "hastings.manuscript.write_guard",
        "sqlite3",
        "yaml",
    },
    "application": {
        "hastings.bible.store",
        "hastings.events_store",
        "hastings.server.reconcile",
        "hastings.manuscript.write_guard",
        "sqlite3",
    },
    "interfaces": {"hastings.events_store", "hastings.server.reconcile"},
}


def _workspace() -> StoryWorkspace:
    return StoryWorkspace(root=FIXTURE.parent, slug=SLUG)


# --------------------------------------------------------------------------
# 1. REST 路由
# --------------------------------------------------------------------------
def _route_table() -> dict[str, set[str]]:
    """通过 OpenAPI 取完整路由表（path → methods），避免惰性 include 前缀解析问题。"""
    schema = build_v2_app(V2Runtime.memory()).openapi()
    return {
        path: {method.upper() for method in operations}
        for path, operations in schema.get("paths", {}).items()
    }


def test_v2_route_table_has_no_legacy_endpoints() -> None:
    table = _route_table()
    assert table and all(path.startswith("/api/v2") for path in table), (
        f"V2 路由未挂载：{sorted(table)}"
    )
    offenders = [path for path in table if any(token in path for token in _LEGACY_ROUTE_TOKENS)]
    assert not offenders, f"V2 路由表出现旧写入口：{offenders}"


def test_v2_write_routes_require_v2_projects() -> None:
    """所有写路由都挂在 /api/v2 前缀下（无 V1 前缀写口）。"""
    for path, methods in _route_table().items():
        if methods & {"POST", "PUT", "PATCH", "DELETE"}:
            assert path.startswith("/api/v2"), f"写路由不在 V2 前缀下：{path}"


# --------------------------------------------------------------------------
# 2. 工具集
# --------------------------------------------------------------------------
def _tool_map() -> dict[str, object]:
    tools = create_writing_tools(_workspace())
    return {tool.name: tool for tool in tools}


@pytest.mark.parametrize("name", sorted(_LEGACY_WRITE_TOOLS))
def test_write_tools_are_retired(name: str) -> None:
    """写工具已随 V1 弃用整体删除：任何工具集中都不再出现。"""
    assert name not in _tool_map(), f"写工具 {name} 仍被装配"


@pytest.mark.parametrize("name", sorted(_READ_TOOLS))
def test_read_tools_remain_available(name: str) -> None:
    """只读工具是 V1 弃用后的存留通道（对照上面写工具的缺席）。"""
    assert name in _tool_map(), f"只读工具 {name} 缺席"


# --------------------------------------------------------------------------
# 3. 模块依赖
# --------------------------------------------------------------------------
def _imported_modules(root: Path) -> set[str]:
    found: set[str] = set()
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module)
    return found


@pytest.mark.parametrize("layer", sorted(_FORBIDDEN))
def test_v2_layers_do_not_import_legacy_paths(layer: str) -> None:
    imported = _imported_modules(SRC / layer)
    clashes = imported & _FORBIDDEN[layer]
    assert not clashes, f"{layer} 层 import 了旧写路径：{sorted(clashes)}"


def test_bible_store_only_read_in_importers() -> None:
    """interfaces 下只有 importers 可触达旧账本读取；其余（API/CLI 适配）不得触达。"""
    offenders: list[str] = []
    for path in (SRC / "interfaces").rglob("*.py"):
        if "__pycache__" in path.parts or path.parts[-2] == "importers":
            continue
        imported = _imported_modules_of(path)
        if "hastings.bible.store" in imported:
            offenders.append(str(path.relative_to(SRC)))
    assert not offenders, f"旧账本读取泄漏到 importers 之外：{offenders}"


def _imported_modules_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_runtime_surface_symbols_absent() -> None:
    """V2 运行时装配不 import 旧运行时模块（对 plan「无 X 作为授权/互斥」的可核查面）。"""
    imported = _imported_modules_of(SRC / "server" / "v2.py")
    legacy = {
        "hastings.server.reconcile",
        "hastings.server.session",
        "hastings.events_store",
        "hastings.bible.store",
        "hastings.manuscript.write_guard",
    }
    assert not (imported & legacy), f"V2 运行时装配 import 了旧模块：{sorted(imported & legacy)}"

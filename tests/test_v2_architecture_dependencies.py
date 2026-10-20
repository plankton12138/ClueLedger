"""V2 架构依赖方向测试（Phase 11 T11.4，计划 §4/§6）。

规则来源：dev-notes/v0.2/modules/README.md §2 与 phase-11 计划 T11.4：

1. ``domain`` 不得 import 其他四层、``hastings.tools`` 和 Tau；
2. ``interfaces`` 不得 import ``infrastructure`` 具体实现；
3. ``agents`` 不得 import ``CommitRepository`` 写端口（只准返回 Proposal）；
4. 任何 V2 包不得 import 旧 ``hastings.tools`` 作为写入口。

扫描基于 AST，按模块路径前缀判断，不引入大型依赖。
同一扫描函数同时服务两个断言：真实 src 树零违规；故意违规的临时树必须被检出
（验收要求 §7：用一个故意违规 fixture 证明测试会失败）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "hastings"

# V2 五层包：存在于 src/hastings 之下，包名即依赖规则的主体。
V2_PACKAGES: tuple[str, ...] = ("domain", "application", "agents", "infrastructure", "interfaces")

# domain 禁止依赖的目标（模块路径前缀）。
_DOMAIN_FORBIDDEN: tuple[str, ...] = (
    "hastings.application",
    "hastings.agents",
    "hastings.infrastructure",
    "hastings.interfaces",
    "hastings.tools",
    "tau_ai",
    "tau_agent",
)


def _qualified_imports(
    tree: ast.Module, rel_parents: list[str]
) -> list[tuple[str, tuple[str, ...]]]:
    """解析一个已 parse 的模块，返回 (导入目标全名, from-import 的名字列表)。

    ``rel_parents`` 为文件相对 hastings 包根的父目录段，例如
    ["domain", "story"]；相对导入据此解析为绝对名。
    名字列表对 ``import x`` 形式为空元组。
    """
    base = ["hastings", *rel_parents]

    results: list[tuple[str, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                results.append((alias.name, ()))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                target = node.module or ""
            else:
                # from . import x / from ..pkg import y：回退 level-1 层
                prefix = base[: len(base) - (node.level - 1)]
                target = ".".join([*prefix, node.module] if node.module else prefix)
            results.append((target, tuple(alias.name for alias in node.names)))
    return results


def _prefix_match(target: str, roots: tuple[str, ...]) -> str | None:
    """target 是否落在某个根模块（或其子模块）内；命中返回该根。"""
    for root in roots:
        if target == root or target.startswith(root + "."):
            return root
    return None


def scan_violations(hastings_root: Path) -> list[str]:
    """扫描 V2 包的依赖违规，返回「文件: 规则」描述列表。"""
    violations: list[str] = []
    for pkg in V2_PACKAGES:
        pkg_dir = hastings_root / pkg
        if not pkg_dir.is_dir():
            continue
        for file in sorted(pkg_dir.rglob("*.py")):
            rel = file.relative_to(hastings_root).as_posix()
            tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
            rel_parents = list(file.parent.relative_to(hastings_root).parts)
            for target, names in _qualified_imports(tree, rel_parents):
                if pkg == "domain" and (hit := _prefix_match(target, _DOMAIN_FORBIDDEN)):
                    violations.append(f"{rel}: domain 依赖了 {hit}")
                if pkg == "interfaces" and _prefix_match(target, ("hastings.infrastructure",)):
                    violations.append(f"{rel}: interfaces 依赖了 infrastructure 具体实现")
                if (
                    pkg == "agents"
                    and _prefix_match(
                        target,
                        ("hastings.application.ports.repository", "hastings.application.ports"),
                    )
                    and "CommitRepository" in names
                ):
                    violations.append(f"{rel}: agents import 了 CommitRepository 写端口")
                if _prefix_match(target, ("hastings.tools",)):
                    violations.append(f"{rel}: V2 包 import 了旧写入口 hastings.tools")
            # 评审收口：import 形式之外的属性式引用（import x as r; r.CommitRepository、
            # from hastings.application import ports; ports.CommitRepository）同样封禁——
            # 规则是「不持有写端口」，不是「不许用某种 import 语法」。
            if pkg == "agents" and _references_write_port(tree):
                violations.append(f"{rel}: agents 引用了 CommitRepository 写端口（属性式）")
    return violations


def _references_write_port(tree: ast.Module) -> bool:
    """检测 AST 中对 CommitRepository 的表达式级引用（Name / Attribute）。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "CommitRepository":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "CommitRepository":
            return True
    return False


def test_v2_packages_exist() -> None:
    """五个 V2 包骨架必须存在（Phase 11 交付的最小结构）。"""
    for pkg in V2_PACKAGES:
        init = SRC_ROOT / pkg / "__init__.py"
        assert init.is_file(), f"缺少 V2 包骨架：src/hastings/{pkg}/__init__.py"


def test_real_tree_has_no_dependency_violations() -> None:
    assert scan_violations(SRC_ROOT) == []


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_deliberate_violations_are_detected(tmp_path: Path) -> None:
    """故意违规的临时树必须逐条命中规则（证明测试本身有效）。"""
    _write(
        tmp_path / "domain" / "evil.py",
        "import hastings.application\nfrom hastings.tools import bible_tools\nimport tau_agent\n",
    )
    _write(tmp_path / "domain" / "clean.py", "from dataclasses import dataclass\n")
    _write(
        tmp_path / "interfaces" / "evil.py",
        "from hastings.infrastructure.repository import sqlite\n",
    )
    _write(
        tmp_path / "agents" / "evil.py",
        "from hastings.application.ports.repository import CommitRepository\n",
    )
    # 评审收口补的漏检形态：import as 别名 + 属性访问，绕过 from-import 名单
    _write(
        tmp_path / "agents" / "indirect.py",
        "import hastings.application.ports.repository as repo\n"
        "\n\n"
        "def f() -> object:\n"
        "    return repo.CommitRepository\n",
    )
    _write(tmp_path / "agents" / "clean.py", "from hastings.application.ports import AgentRunner\n")

    violations = scan_violations(tmp_path)

    assert any("domain/evil.py" in v and "hastings.application" in v for v in violations)
    assert any("domain/evil.py" in v and "hastings.tools" in v for v in violations)
    assert any("domain/evil.py" in v and "tau_agent" in v for v in violations)
    assert any("interfaces/evil.py" in v and "infrastructure" in v for v in violations)
    assert any("agents/evil.py" in v and "CommitRepository" in v for v in violations)
    assert any("agents/indirect.py" in v and "CommitRepository" in v for v in violations)
    # clean 文件与相对导入不得误报
    assert not any("clean.py" in v for v in violations)


def test_relative_imports_are_resolved(tmp_path: Path) -> None:
    """from . / from .. 的相对导入必须解析到绝对包名后再判定。"""
    _write(tmp_path / "application" / "sub" / "evil.py", "from ..domain import models\n")
    _write(tmp_path / "domain" / "sibling.py", "from . import helper\n")

    violations = scan_violations(tmp_path)

    # application/sub 内 from ..domain 属 application->domain，合法方向，不报
    assert not any("application/sub/evil.py" in v for v in violations)
    # 反向场景：domain 内 from ..application 必须命中
    _write(tmp_path / "domain" / "evil2.py", "from ..application.ports import Clock\n")
    violations = scan_violations(tmp_path)
    assert any("domain/evil2.py" in v and "hastings.application" in v for v in violations)


@pytest.mark.parametrize(
    "pkg",
    V2_PACKAGES,
)
def test_v2_packages_do_not_import_legacy_tools(pkg: str) -> None:
    """逐包断言：V2 包不得 import 旧 hastings.tools 作为写入口。"""
    pkg_dir = SRC_ROOT / pkg
    assert pkg_dir.is_dir()
    violations = [
        v for v in scan_violations(SRC_ROOT) if v.startswith(f"{pkg}/") and "hastings.tools" in v
    ]
    assert violations == []

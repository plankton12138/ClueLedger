"""Suite / Env / Evaluator 资产加载。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from evaluation.harness.schema import CaseLoadError, Check

EVAL_ROOT = Path(__file__).resolve().parents[1]


class AssetLoadError(ValueError):
    """资产 YAML 无法加载。"""


@dataclass(frozen=True)
class EvaluatorPack:
    id: str
    version: int
    desc: str
    checks: tuple[Check, ...]
    source: Path


@dataclass(frozen=True)
class EnvProfile:
    id: str
    version: int
    desc: str
    capture_inputs: bool
    budget: int
    timeout_s: float
    work_baseline: str
    source: Path


@dataclass(frozen=True)
class Suite:
    id: str
    version: int
    desc: str
    case_ids: tuple[str, ...]
    default_env: str
    default_evaluator: str
    mode: Literal["smoke", "gate"]
    repeat: int
    source: Path


def _require(mapping: dict[str, Any], key: str, *, where: str) -> Any:
    if key not in mapping:
        raise AssetLoadError(f"{where} 缺少字段 {key}")
    return mapping[key]


def _load_yaml(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise AssetLoadError(f"{path} 根须为对象")
    return raw


def _parse_check_item(item: dict[str, Any], *, where: str) -> Check:
    level = str(_require(item, "level", where=where))
    if level not in {"must", "quality", "human"}:
        raise AssetLoadError(f"{where}.level 无效")
    role_raw = item.get("role", "required")
    role = str(role_raw)
    if role not in {"required", "diagnostic"}:
        raise AssetLoadError(f"{where}.role 须为 required 或 diagnostic")
    question = item.get("question")
    if level == "human" and not question:
        raise AssetLoadError(f"{where} 人工项须有 question")
    impl = item.get("impl")
    return Check(
        id=str(_require(item, "id", where=where)),
        level=level,  # type: ignore[arg-type]
        role=role,  # type: ignore[arg-type]
        question=str(question) if question is not None else None,
        impl=str(impl) if impl is not None else None,
    )


def load_evaluator(path: Path) -> EvaluatorPack:
    raw = _load_yaml(path)
    where = str(path)
    checks_raw = _require(raw, "checks", where=where)
    if not isinstance(checks_raw, list) or not checks_raw:
        raise AssetLoadError(f"{where} checks 须为非空列表")
    checks: list[Check] = []
    for index, item in enumerate(checks_raw):
        if not isinstance(item, dict):
            raise AssetLoadError(f"{where} checks[{index}] 须为对象")
        checks.append(_parse_check_item(item, where=f"{where} checks[{index}]"))
    return EvaluatorPack(
        id=str(_require(raw, "id", where=where)),
        version=int(_require(raw, "version", where=where)),
        desc=str(raw.get("desc") or ""),
        checks=tuple(checks),
        source=path,
    )


def load_evaluator_by_id(evaluator_id: str, *, evaluators_dir: Path | None = None) -> EvaluatorPack:
    root = evaluators_dir or (EVAL_ROOT / "evaluators")
    path = root / f"{evaluator_id}.yaml"
    if not path.is_file():
        path = root / f"{evaluator_id}.yml"
    if not path.is_file():
        raise AssetLoadError(f"找不到 Evaluator 包：{evaluator_id}（目录 {root}）")
    pack = load_evaluator(path)
    if pack.id != evaluator_id:
        raise AssetLoadError(f"{path} 的 id={pack.id} 与文件名 {evaluator_id} 不一致")
    return pack


def load_env(path: Path) -> EnvProfile:
    raw = _load_yaml(path)
    where = str(path)
    return EnvProfile(
        id=str(_require(raw, "id", where=where)),
        version=int(_require(raw, "version", where=where)),
        desc=str(raw.get("desc") or ""),
        capture_inputs=bool(raw.get("capture_inputs", True)),
        budget=int(raw.get("budget") or 30),
        timeout_s=float(raw.get("timeout_s") or 300.0),
        work_baseline=str(raw.get("work_baseline") or "base"),
        source=path,
    )


def load_env_by_id(env_id: str, *, envs_dir: Path | None = None) -> EnvProfile:
    root = envs_dir or (EVAL_ROOT / "envs")
    path = root / f"{env_id}.yaml"
    if not path.is_file():
        path = root / f"{env_id}.yml"
    if not path.is_file():
        raise AssetLoadError(f"找不到评测环境：{env_id}（目录 {root}）")
    profile = load_env(path)
    if profile.id != env_id:
        raise AssetLoadError(f"{path} 的 id={profile.id} 与文件名 {env_id} 不一致")
    return profile


def load_suite(path: Path) -> Suite:
    raw = _load_yaml(path)
    where = str(path)
    cases_raw = _require(raw, "cases", where=where)
    if not isinstance(cases_raw, list) or not cases_raw:
        raise AssetLoadError(f"{where} cases 须为非空列表")
    mode = str(raw.get("mode") or "smoke")
    if mode not in {"smoke", "gate"}:
        raise AssetLoadError(f"{where} mode 须为 smoke 或 gate")
    return Suite(
        id=str(_require(raw, "id", where=where)),
        version=int(_require(raw, "version", where=where)),
        desc=str(raw.get("desc") or ""),
        case_ids=tuple(str(item) for item in cases_raw),
        default_env=str(_require(raw, "default_env", where=where)),
        default_evaluator=str(_require(raw, "default_evaluator", where=where)),
        mode=mode,  # type: ignore[arg-type]
        repeat=int(raw.get("repeat") or (3 if mode == "gate" else 1)),
        source=path,
    )


def load_suite_by_id(suite_id: str, *, suites_dir: Path | None = None) -> Suite:
    root = suites_dir or (EVAL_ROOT / "suites")
    path = root / f"{suite_id}.yaml"
    if not path.is_file():
        path = root / f"{suite_id}.yml"
    if not path.is_file():
        raise AssetLoadError(f"找不到 Suite：{suite_id}（目录 {root}）")
    suite = load_suite(path)
    if suite.id != suite_id:
        raise AssetLoadError(f"{path} 的 id={suite.id} 与文件名 {suite_id} 不一致")
    return suite


def list_yaml_assets(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml"))


def list_suites(*, suites_dir: Path | None = None) -> list[Suite]:
    root = suites_dir or (EVAL_ROOT / "suites")
    return [load_suite(path) for path in list_yaml_assets(root)]


def list_envs(*, envs_dir: Path | None = None) -> list[EnvProfile]:
    root = envs_dir or (EVAL_ROOT / "envs")
    return [load_env(path) for path in list_yaml_assets(root)]


def list_evaluators(*, evaluators_dir: Path | None = None) -> list[EvaluatorPack]:
    root = evaluators_dir or (EVAL_ROOT / "evaluators")
    return [load_evaluator(path) for path in list_yaml_assets(root)]


def resolve_checks_for_case(
    raw: dict[str, Any],
    *,
    where: str,
    evaluators_dir: Path | None = None,
) -> tuple[tuple[Check, ...], str | None, int | None]:
    """从案例 YAML 解析检查项；返回 (checks, evaluator_id, evaluator_version)。"""
    evaluator_id = raw.get("evaluator")
    checks_extra = raw.get("checks_extra") or []
    inline = raw.get("checks")

    if evaluator_id is not None:
        pack = load_evaluator_by_id(str(evaluator_id), evaluators_dir=evaluators_dir)
        by_id = {check.id: check for check in pack.checks}
        if not isinstance(checks_extra, list):
            raise CaseLoadError(f"{where} checks_extra 须为列表")
        for index, item in enumerate(checks_extra):
            if not isinstance(item, dict):
                raise CaseLoadError(f"{where} checks_extra[{index}] 须为对象")
            extra = _parse_check_item(item, where=f"{where} checks_extra[{index}]")
            by_id[extra.id] = extra
        # 保持包内顺序，再追加仅出现在 extra 的新 id
        ordered = list(pack.checks)
        pack_ids = {c.id for c in pack.checks}
        for check_id, check in by_id.items():
            if check_id not in pack_ids:
                ordered.append(check)
            else:
                # 覆盖同 id
                ordered = [check if c.id == check_id else c for c in ordered]
        return tuple(ordered), pack.id, pack.version

    if not isinstance(inline, list) or not inline:
        raise CaseLoadError(f"{where} 须提供 evaluator 或非空 checks")
    checks: list[Check] = []
    for index, item in enumerate(inline):
        if not isinstance(item, dict):
            raise CaseLoadError(f"{where} checks[{index}] 须为对象")
        try:
            checks.append(_parse_check_item(item, where=f"{where} checks[{index}]"))
        except AssetLoadError as exc:
            raise CaseLoadError(str(exc)) from exc
    return tuple(checks), None, None

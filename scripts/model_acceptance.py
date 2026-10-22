"""在线任务验收：逐轮核对实际修改、读取、完整报告及未覆盖路径。不自动重试。

uv run python scripts/model_acceptance.py --case index_recall --case index_stale
每个样例使用独立作品和状态；报告不收录凭据或整章正文。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hastings.server.__main__ import DEFAULT_CONFIG_PATH, build_credential_store, load_provider_book
from hastings.server.file_app import configured_provider_factory
from hastings.server.provider_runtime import is_loopback_url
from hastings.workspace.chapter_index import ChapterIndexStore
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.git import GitRepo
from hastings.workspace.materials import ContextRequest, merge_spans
from hastings.workspace.review import ReviewStore
from hastings.workspace.service import TurnService
from hastings.workspace.state import ConversationStore, conversation_root

FIRST = "正文/0001 雨夜.md"
SECOND = "正文/0002 走廊.md"
THIRD = "正文/0003 书房.md"
FOURTH = "正文/0004 客房.md"
CHARACTER = "设定集/角色小传/林远.md"
TRUTH = "设定集/真相.md"
CLUES = "设定集/线索.md"
_FIXTURES = {
    FIRST: "# 雨夜\n\n侦探抵达旅馆时，雪已经下了三天。\n",
    SECOND: "# 走廊\n\n走廊尽头的灯忽明忽暗。\n",
    THIRD: "# 书房\n\n侦探推开门，桌上有一盏冷掉的茶。\n窗外雪停了。\n",
    FOURTH: "# 客房\n\n林远没有进过书房，他一直待在客房。\n",
    CHARACTER: "# 林远\n\n身份：私家侦探。曾经担任刑警。\n",
    TRUTH: "# 真相\n\n凶手是旅馆管家。\n",
    CLUES: "# 线索\n\n- 前台的手套是湿的\n",
}


@dataclass(frozen=True)
class Case:
    key: str
    family: str
    title: str
    prompt: str
    allowed_changes: tuple[str, ...] = ()
    required_changes: tuple[str, ...] = ()
    expected_reads: tuple[str, ...] = ()
    forbidden_reads: tuple[str, ...] = ()
    expected_text: dict[str, str] = field(default_factory=dict)
    fixtures: dict[str, str] = field(default_factory=dict)
    selected: str | None = None
    references: tuple[str, ...] = ()
    purpose: str = "chat"
    index_paths: tuple[str, ...] = ()
    follow_up: str | None = None
    change_source: bool = False
    clarification: bool = False
    require_review: bool = False


CASES = (
    Case(
        "refs_locate",
        "自然语言参考",
        "不选文件，参考第一章修改第三章",
        "参考第一章《雨夜》，把第三章『桌上有一盏冷掉的茶』改为『桌上那盏茶已经凉透』。只改第三章。",
        allowed_changes=(THIRD,),
        required_changes=(THIRD,),
        expected_reads=(FIRST, THIRD),
        expected_text={THIRD: "桌上那盏茶已经凉透"},
    ),
    Case(
        "reference_not_authority",
        "选择参考",
        "小传是参考，修改当前章",
        "修改这一章，把『侦探推开门』改为『侦探推门而入』。",
        allowed_changes=(THIRD,),
        required_changes=(THIRD,),
        expected_reads=(THIRD,),
        selected=THIRD,
        references=(CHARACTER,),
        expected_text={THIRD: "侦探推门而入"},
    ),
    Case(
        "fixed_scope",
        "固定范围",
        "只改第三章，不动人物设定",
        "第三章写林远进过书房，第四章说他没进去。只把第三章『林远进过书房』改为『林远没有进过书房』，"
        "人物设定、真相和其他章节都不许动。",
        allowed_changes=(THIRD,),
        required_changes=(THIRD,),
        expected_reads=(THIRD,),
        fixtures={THIRD: "# 书房\n\n林远进过书房。\n"},
        expected_text={THIRD: "林远没有进过书房"},
    ),
    Case(
        "sync_chapters",
        "关联修改",
        "修改人物动机并同步相关章节",
        "把林远调查的动机从『寻找失踪的弟弟』改为『查明父亲死因』，并同步已经写到该动机的章节。"
        "先查找和读取，只修改出现原动机的小传和章节，使用『查明父亲死因』这个表述。",
        allowed_changes=(CHARACTER, THIRD, FOURTH),
        required_changes=(CHARACTER, THIRD, FOURTH),
        expected_reads=(CHARACTER, THIRD, FOURTH),
        fixtures={
            CHARACTER: "# 林远\n\n身份：私家侦探。调查动机：寻找失踪的弟弟。\n",
            THIRD: "# 书房\n\n林远为寻找失踪的弟弟进入书房。\n",
            FOURTH: "# 客房\n\n林远说，他来旅馆是为了寻找失踪的弟弟。\n",
        },
        expected_text={p: "查明父亲死因" for p in (CHARACTER, THIRD, FOURTH)},
    ),
    Case(
        "discuss_only",
        "任务理解",
        "假设凶手，只讨论",
        "如果凶手其实是林远，故事会怎么走？先说说，不要改任何文件。",
    ),
    Case(
        "read_only_check",
        "任务理解",
        "只检查，不修改",
        "请读取第三章，检查叙述是否自洽，直接在回复里说明。只检查，不修改任何文件。",
        expected_reads=(THIRD,),
    ),
    Case(
        "continue_unanchored",
        "任务理解",
        "新会话里的继续没有承接对象",
        "继续。",
        clarification=True,
    ),
    Case(
        "ambiguous_character",
        "任务理解",
        "同名人物需要澄清",
        "把林远的小传职业改成记者。",
        clarification=True,
        fixtures={"设定集/角色小传/林远（同名）.md": "# 林远\n\n身份：医生。与侦探林远同名。\n"},
    ),
    Case(
        "negative_reference",
        "任务理解",
        "否定参考不应被当成必读",
        "只把第三章『窗外雪停了』改成『窗外又飘起雪』。不需要参考第一章，也不要读取第一章。",
        allowed_changes=(THIRD,),
        required_changes=(THIRD,),
        expected_reads=(THIRD,),
        forbidden_reads=(FIRST,),
        expected_text={THIRD: "窗外又飘起雪"},
    ),
    Case(
        "index_recall",
        "索引与原文",
        "索引后仍读取原文并写作",
        "给第一章建立内容索引。",
        purpose="update_chapter_index",
        index_paths=(FIRST,),
        allowed_changes=(THIRD,),
        required_changes=(THIRD,),
        follow_up="参考第一章的天气，读取原文后把第三章『窗外雪停了』改为『窗外的雪已经下了三天』，只改第三章。",
        expected_text={THIRD: "窗外的雪已经下了三天"},
    ),
    Case(
        "index_stale",
        "索引与原文",
        "源文件改变后旧索引失效",
        "给第一章建立内容索引。",
        purpose="update_chapter_index",
        index_paths=(FIRST,),
        allowed_changes=(THIRD,),
        required_changes=(THIRD,),
        change_source=True,
        follow_up="第一章被外部修改过。重新读取其原文，按当前天气把第三章末句改为『窗外的雪已经停了三天』，只改第三章。",
        expected_text={THIRD: "窗外的雪已经停了三天"},
    ),
    Case(
        "review_after_write",
        "写后检查",
        "修改完成后形成完整检查报告",
        "只把第三章『窗外雪停了』改为『窗外又飘起雪』，改完检查第三章与第一章有没有前后矛盾。",
        allowed_changes=(THIRD,),
        required_changes=(THIRD,),
        require_review=True,
        expected_text={THIRD: "窗外又飘起雪"},
    ),
)


def _seed(workspace: Workspace, path: str, text: str) -> None:
    try:
        digest = workspace.read(path).digest
    except WorkspaceError:
        digest = None
    workspace.write(path, text, expected_digest=digest)


def _digests(workspace: Workspace) -> dict[str, str]:
    catalog = workspace.catalog()
    return {
        e.path: workspace.read(e.path).digest
        for group in (catalog.chapters, catalog.characters, catalog.settings)
        for e in group
        if e.exists
    }


def _changed(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))


def _check(
    name: str, ok: bool, detail: Any, *, status: str | None = None, required: bool = True
) -> dict[str, Any]:
    return {
        "name": name,
        "ok": ok,
        "status": status or ("pass" if ok else "fail"),
        "required": required,
        "detail": detail,
    }


def result_status(checks: list[dict[str, Any]]) -> str:
    states = [c["status"] for c in checks if c["required"]]
    if "fail" in states:
        return "fail"
    if any(s in ("partial", "not_covered") for s in states):
        return "partial"
    return "pass" if states else "not_covered"


def _finished(events: list[dict[str, Any]]) -> dict[str, Any]:
    return next((e for e in reversed(events) if e.get("type") == "run_finished"), {})


def _read_paths(events: list[dict[str, Any]]) -> set[str]:
    return {str(e["path"]) for e in events if e.get("type") == "material_read" and e.get("path")}


def _public_text(events: list[dict[str, Any]]) -> str:
    return "\n".join(str(e.get("text", "")) for e in events if e.get("type") == "assistant_message")


def _scope_errors(events: list[dict[str, Any]], existing: set[str]) -> list[str]:
    """用当时生效的操作范围核对写入，不能用后来的范围追认。"""
    grants: set[tuple[str, str, str | None]] = set()
    denied: set[str] = set()
    errors = []
    for event in events:
        kind = event.get("type")
        if kind in ("task_scope_set", "task_scope_extended"):
            grants = {(g["action"], g["path"], g.get("target")) for g in event.get("grants", [])}
            denied = set(event.get("denied_paths", []))
        if kind != "tool_finished" or event.get("ok") is not True:
            continue
        tool, args = event.get("tool"), event.get("args", {})
        if tool not in ("write_file", "edit_file", "move_file", "delete_file"):
            continue
        path = args.get("source") if tool == "move_file" else args.get("path")
        target = args.get("target") if tool == "move_file" else None
        action = {"edit_file": "update", "move_file": "move", "delete_file": "delete"}.get(
            tool, "update" if path in existing else "create"
        )
        if (action, path, target) not in grants or {path, target} & denied:
            errors.append(f"{tool}:{path}:{target}")
        if tool in ("delete_file", "move_file"):
            existing.discard(path)
        if tool == "move_file":
            existing.add(target)
        elif tool == "write_file":
            existing.add(path)
    return errors


def _review_checks(
    events: list[dict[str, Any]], workspace: Workspace, store: ConversationStore
) -> list[dict[str, Any]]:
    event = next((e for e in reversed(events) if e.get("type") == "review_finished"), {})
    state = event.get("status")
    rows = [
        _check(
            "检查已完成",
            state in ("findings", "no_findings"),
            state,
            status="partial" if state == "incomplete" else None,
        )
    ]
    report = next(
        (
            r
            for r in ReviewStore(store.root).reports(workspace)
            if r.report_id == event.get("report_id")
        ),
        None,
    )
    rows.append(
        _check(
            "事件对应已保存且当前有效的报告",
            report is not None and report.freshness == "valid",
            event.get("report_id"),
            status="partial" if state == "incomplete" and report is None else None,
        )
    )
    if report is None:
        return rows
    for path, digest in report.target_digests:
        rows.append(_check(f"报告目标版本:{path}", workspace.read(path).digest == digest, digest))
    for finding in report.findings:
        for evidence in finding.evidence:
            view = workspace.read(evidence.path)
            spans = merge_spans(
                (e["start_char"], e["end_char"])
                for e in events
                if e.get("type") == "material_read"
                and e.get("path") == evidence.path
                and e.get("digest") == evidence.digest
            )
            covered = any(a <= evidence.start < evidence.end <= b for a, b in spans)
            matches = " ".join(view.text[evidence.start : evidence.end].split()) == " ".join(
                evidence.quote.split()
            )
            rows.append(
                _check(
                    f"证据版本、引文及读取区间:{evidence.path}",
                    view.digest == evidence.digest and matches and covered,
                    {"range": [evidence.start, evidence.end], "provided": spans},
                )
            )
    return rows


def _checks(
    case: Case,
    workspace: Workspace,
    store: ConversationStore,
    events: list[dict[str, Any]],
    changed: list[str],
    follow: list[dict[str, Any]],
    *,
    before_paths: set[str] | None = None,
    initial_changed: list[str] | None = None,
    index_before_follow: str | None = None,
) -> list[dict[str, Any]]:
    rows = []
    stages = [("首轮", events)] + ([("后续轮", follow)] if case.follow_up else [])
    for name, stage in stages:
        terminal = _finished(stage)
        state = terminal.get("status")
        rows.append(
            _check(
                f"{name}完成",
                state == "completed",
                terminal,
                status="partial" if state == "partial" else None,
            )
        )
        errors = _scope_errors(stage, set(before_paths or _FIXTURES))
        rows.append(_check(f"{name}实际操作符合当时范围", not errors, errors))
    outside = sorted(set(changed) - set(case.allowed_changes))
    rows.append(_check("没有范围外改动", not outside, outside))
    missing = sorted(set(case.required_changes) - set(changed))
    rows.append(_check("要求修改的文件确实改变", not missing, missing))
    active = follow if case.follow_up else events
    for path in case.expected_reads:
        rows.append(
            _check(f"读取原文:{path}", path in _read_paths(active), sorted(_read_paths(active)))
        )
    for path in case.forbidden_reads:
        rows.append(_check(f"没有读取被排除材料:{path}", path not in _read_paths(active), path))
    for path, text in case.expected_text.items():
        rows.append(_check(f"落盘内容包含本次要求:{path}", text in workspace.read(path).text, text))
    if not case.allowed_changes:
        rows.append(
            _check(
                "已给作者公开回答", bool(_public_text(events).strip()), _public_text(events)[:300]
            )
        )
    if case.clarification:
        answer = _public_text(events)
        terms = ("？", "?", "请", "哪", "明确", "确认", "提供", "无法", "没有", "缺少")
        rows.append(_check("存在澄清或缺少承接说明", any(t in answer for t in terms), answer[:400]))
    if case.references:
        prepared = next((e for e in events if e.get("type") == "context_prepared"), {})
        refs = {r["path"] for r in prepared.get("references", [])}
        rows.append(_check("作者选择参考已登记", set(case.references) <= refs, sorted(refs)))
    if case.key == "sync_chapters":
        extended = [e for e in events if e.get("type") == "task_scope_extended"]
        rows.append(
            _check(
                "在线范围追加路径",
                bool(extended),
                f"观察到 {len(extended)} 次追加",
                status="pass" if extended else "not_covered",
                required=False,
            )
        )
    if case.index_paths:
        rows.append(_check("索引轮没有改作品", initial_changed == [], initial_changed))
        rows.append(
            _check(
                "索引成功发布",
                any(
                    e.get("type") == "chapter_index_updated" and e.get("status") == "valid"
                    for e in events
                ),
                case.index_paths,
            )
        )
        rows.append(
            _check(
                "后续轮重新读取第一章原文",
                FIRST in _read_paths(follow),
                sorted(_read_paths(follow)),
            )
        )
        expected = workspace.read(FIRST).digest
        rows.append(
            _check(
                "后续读取使用当前源版本",
                any(
                    e.get("type") == "material_read"
                    and e.get("path") == FIRST
                    and e.get("digest") == expected
                    for e in follow
                ),
                expected,
            )
        )
        if case.change_source:
            rows.append(
                _check("源变更后索引确实过期", index_before_follow == "stale", index_before_follow)
            )
            rows.append(
                _check(
                    "旧索引未被自动更新",
                    not any(e.get("type") == "chapter_index_updated" for e in follow),
                    "只能由作者显式更新",
                )
            )
    if case.require_review:
        rows.extend(_review_checks(events, workspace, store))
    return rows


def _brief(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keep = {
        "turn_started",
        "context_prepared",
        "reference_registered",
        "material_read",
        "task_scope_set",
        "task_scope_extended",
        "tool_finished",
        "chapter_index_updated",
        "review_requested",
        "review_finished",
        "run_finished",
        "error",
        "assistant_message",
    }
    return [
        {k: v for k, v in e.items() if k not in ("text", "summary")}
        | ({"text": str(e.get("text", ""))[:1200]} if e["type"] == "assistant_message" else {})
        for e in events
        if e.get("type") in keep
    ]


class Runner:
    def __init__(self, work: Path, factory: Any, model: str) -> None:
        self.work, self.factory, self.model = work, factory, model

    async def run(self, case: Case) -> dict[str, Any]:
        book, state = self.work / case.key / "作品", self.work / case.key / "state"
        if book.exists() or state.exists():
            raise FileExistsError("验收目录已存在，请使用新的运行目录")
        workspace = Workspace.create(book)
        for path, text in (_FIXTURES | case.fixtures).items():
            _seed(workspace, path, text)
        GitRepo(workspace.root).init()
        store = ConversationStore(conversation_root(state, "w1"))
        service = TurnService(workspace, store, self.factory, max_turns=24)
        before = _digests(workspace)
        info = await service.accept(
            case.prompt,
            f"k-{case.key}",
            context=ContextRequest(selected_path=case.selected, reference_paths=case.references),
            purpose=case.purpose,
            index_paths=case.index_paths,
        )
        await service.wait(info.turn_id)
        events = [e for e in store.events(info.conversation_id) if e.get("turn_id") == info.turn_id]
        initial_changed = _changed(before, _digests(workspace))
        follow: list[dict[str, Any]] = []
        index_before_follow = None
        changed = initial_changed
        if case.follow_up:
            if case.change_source:
                _seed(workspace, FIRST, "# 雨夜\n\n侦探抵达旅馆时，雪已经停了三天。\n")
            index = ChapterIndexStore(store.root).find(workspace, FIRST)
            index_before_follow = index.status if index else "missing"
            follow_before = _digests(workspace)
            follow_info = await service.accept(
                case.follow_up, f"k-{case.key}-2", conversation_id=info.conversation_id
            )
            await service.wait(follow_info.turn_id)
            follow = [
                e
                for e in store.events(info.conversation_id)
                if e.get("turn_id") == follow_info.turn_id
            ]
            changed = sorted(set(changed) | set(_changed(follow_before, _digests(workspace))))
        checks = _checks(
            case,
            workspace,
            store,
            events,
            changed,
            follow,
            before_paths=set(before),
            initial_changed=initial_changed,
            index_before_follow=index_before_follow,
        )
        return {
            "key": case.key,
            "family": case.family,
            "title": case.title,
            "model": self.model,
            "prompt": case.prompt,
            "follow_up": case.follow_up,
            "changed": changed,
            "before": before,
            "after": _digests(workspace),
            "workspace": str(book.resolve()),
            "checks": checks,
            "status": result_status(checks),
            "events": _brief(events),
            "follow_events": _brief(follow),
        }


def coverage(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {r["key"]: r for r in results}
    rows = []
    for family in dict.fromkeys(c.family for c in CASES):
        expected = [c.key for c in CASES if c.family == family]
        states = {k: by_key[k]["status"] if k in by_key else "not_covered" for k in expected}
        state = (
            "fail"
            if "fail" in states.values()
            else ("pass" if set(states.values()) == {"pass"} else "partial")
        )
        rows.append({"family": family, "status": state, "cases": states})
    return rows


def _write(out: Path, results: list[dict[str, Any]], model: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    report = {"model": model, "results": results, "coverage": coverage(results)}
    (out / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    labels = {"pass": "通过", "fail": "失败", "partial": "部分完成", "not_covered": "未覆盖"}
    lines = [f"# 在线任务验收（{model}）", "", "| 类别 | 覆盖状态 |", "|---|---|"]
    lines += [f"| {r['family']} | {labels[r['status']]} |" for r in report["coverage"]]
    for item in results:
        lines += [
            "",
            f"## {item['title']}（{labels[item['status']]}）",
            "",
            f"- 要求：{item['prompt']}",
            f"- 改动：{'、'.join(item.get('changed', [])) or '无'}",
        ]
        if item.get("follow_up"):
            lines.append(f"- 后续要求：{item['follow_up']}")
        for check in item["checks"]:
            lines.append(f"- {labels[check['status']]}：{check['name']}（{check['detail']}）")
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _run_all(cases: tuple[Case, ...], work: Path, out: Path, model: str | None) -> int:
    book, _, _ = load_provider_book(DEFAULT_CONFIG_PATH, model, build_credential_store())
    selection = book.selection()
    profile = book.profile(selection.connection_id) if selection.connection_id else None
    if profile is None or is_loopback_url(profile.base_url):
        raise ValueError("在线验收需要 config.yaml 中选择的在线模型连接")
    runner = Runner(work, configured_provider_factory(book), selection.model)
    results: list[dict[str, Any]] = []
    for case in cases:
        print(f"[case] {case.key} …", flush=True)
        started = time.monotonic()
        try:
            result = await runner.run(case)
        except Exception as exc:
            result = {
                "key": case.key,
                "family": case.family,
                "title": case.title,
                "prompt": case.prompt,
                "status": "fail",
                "checks": [_check("执行未异常", False, f"{type(exc).__name__}: {exc}")],
            }
        result["duration_seconds"] = round(time.monotonic() - started, 1)
        results.append(result)
        _write(out, results, selection.model)
        print(f"[case] {case.key} -> {result['status']}", flush=True)
        if any(
            e.get("type") == "error" and str(e.get("code", "")).startswith("MODEL_")
            for e in result.get("events", [])
        ):
            print("模型调用失败，停止本批后续调用；未执行样例记为未覆盖。", flush=True)
            break
    return int(any(r["status"] != "pass" for r in results))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="dev-notes/v0.6/artifacts/model-acceptance")
    parser.add_argument("--case", action="append", choices=[c.key for c in CASES], default=[])
    parser.add_argument("--model", default=None)
    args = parser.parse_args()
    selected = tuple(c for c in CASES if not args.case or c.key in args.case)
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{time.time_ns() % 1_000_000:06d}"
    return asyncio.run(
        _run_all(
            selected,
            Path("data/test-tmp/model-acceptance") / stamp,
            Path(args.out) / stamp,
            args.model,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())

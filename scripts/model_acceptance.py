"""七类真实模型任务验收（0.6 V06-D2）。

用在线模型跑真实的写作轮次，自动核对可判定的事实：哪些文件被改、禁止文件有没有动、
读过哪些文件、范围怎么追加、检查报告的证据指向哪里、Git 里提交了什么。文字好不好、
伏笔自不自然不在这里判断——样例留给作者看。

用法：

    uv run python scripts/model_acceptance.py --out dev-notes/v0.6/artifacts/model-acceptance
    uv run python scripts/model_acceptance.py --case refs_locate --case review_after_write

每个用例一份独立作品与独立状态目录，结果写在 ``--out/<时间>/`` 下：``report.json``
（逐条检查与事件）与 ``report.md``（给人看的结论表）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hastings.server.__main__ import (
    DEFAULT_CONFIG_PATH,
    build_credential_store,
    load_provider_book,
)
from hastings.server.file_app import configured_provider_factory
from hastings.workspace.chapter_index import ChapterIndexStore
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.git import GitRepo
from hastings.workspace.materials import ContextRequest
from hastings.workspace.service import TurnService
from hastings.workspace.state import ConversationStore, conversation_root

__all__ = ["main"]

BODY = "正文/"
CHARACTER = "设定集/角色小传/林远.md"
TRUTH = "设定集/真相.md"
CLUES = "设定集/线索.md"

_FIXTURES: dict[str, str] = {
    "正文/0001 雨夜.md": "# 雨夜\n\n侦探抵达旅馆时，雪已经下了三天。\n",
    "正文/0002 走廊.md": "# 走廊\n\n走廊尽头的灯忽明忽暗。\n",
    "正文/0003 书房.md": "# 书房\n\n侦探推开门，桌上有一盏冷掉的茶。\n窗外雪停了。\n",
    "正文/0004 客房.md": "# 客房\n\n林远没有进过书房，他一直待在客房。\n",
    CHARACTER: "# 林远\n\n身份：私家侦探。曾经担任刑警。\n",
    TRUTH: "# 真相\n\n凶手是旅馆管家。\n",
    CLUES: "# 线索\n\n- 前台的手套是湿的\n",
}


@dataclass
class Case:
    """一类验收任务：提示语、上下文与要核对的事实。"""

    key: str
    title: str
    prompt: str
    purpose: str = "chat"
    selected: str | None = None
    references: tuple[str, ...] = ()
    index_paths: tuple[str, ...] = ()
    follow_up: str | None = None
    forbidden: tuple[str, ...] = ()
    allowed_changes: tuple[str, ...] = ()
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


CASES: tuple[Case, ...] = (
    Case(
        key="refs_locate",
        title="不选文件，参考第一章修改第三章",
        prompt="参考第一章《雨夜》的写法，把第三章《书房》里「桌上有一盏冷掉的茶」改成一句更冷的描写。",
        forbidden=("正文/0001 雨夜.md",),
        allowed_changes=("正文/0003 书房.md",),
        notes="模型应从话里定位到第一章，只改第三章。",
    ),
    Case(
        key="reference_not_authority",
        title="只选人物小传，说「修改这一章」",
        prompt="修改这一章，把第一句写得更紧凑。",
        selected="正文/0003 书房.md",
        references=(CHARACTER,),
        forbidden=(CHARACTER,),
        allowed_changes=("正文/0003 书房.md",),
        notes="参考不产生授权：小传只读，改动只落在当前阅读的章节。",
    ),
    Case(
        key="fixed_scope",
        title="只改第三章，不动人物设定",
        prompt=(
            "第四章说林远没进过书房，和第三章结尾对不上。请只改第三章，让它自洽，"
            "人物设定和真相都不许动。"
        ),
        forbidden=(CHARACTER, TRUTH),
        allowed_changes=("正文/0003 书房.md",),
        notes="契约明确限制在第三章；诱发跨文件调整的矛盾不应导致改设定。",
    ),
    Case(
        key="sync_chapters",
        title="修改动机并同步相关章节",
        prompt=(
            "第三章里林远发现茶水是凉的，这个细节要在相关章节里也交代一下。"
            "如果需要动其他章节，先读过再说明为什么相关。"
        ),
        notes="可发现范围：允许追加，但追加必须带读过文件的原文依据。",
    ),
    Case(
        key="discuss_only",
        title="假设凶手、只检查、继续（不给承接对象）",
        prompt="如果凶手其实是林远，故事会怎么走？先说说，不要改任何文件。",
        allowed_changes=(),
        notes="讨论不写文件。",
    ),
    Case(
        key="index_recall",
        title="索引之后回到原文呼应",
        prompt="先给第一章《雨夜》建索引。",
        purpose="update_chapter_index",
        index_paths=("正文/0001 雨夜.md",),
        follow_up="第三章要写他第一次进书房时看到的东西，和第一章的天气呼应一下。",
        notes="索引任务只读；后续写作要靠索引/搜索回到原文，不能凭印象。",
    ),
    Case(
        key="review_after_write",
        title="改完检查",
        prompt=(
            "把第三章《书房》里「窗外雪停了」改成「窗外又飘起雪」，"
            "再在第第四章加一句林远已经死了的回忆，改完检查有没有前后矛盾。"
        ),
        notes="检查要给出报告；疑点与真实冲突都要有原文证据。",
    ),
)


class Runner:
    """跑一个用例：准备作品、受理、收集事件、核对事实。"""

    def __init__(self, work: Path, factory: Any, model: str) -> None:
        self.work = work
        self.factory = factory
        self.model = model

    async def run(self, case: Case) -> dict[str, Any]:
        book = self.work / case.key / "雪山旅馆"
        state = self.work / case.key / "state"
        if book.exists():
            shutil.rmtree(book)
        if state.exists():
            shutil.rmtree(state)
        workspace = Workspace.create(book)
        for path, text in _FIXTURES.items():
            _seed(workspace, path, text)
        GitRepo(workspace.root).init()
        store = ConversationStore(conversation_root(state, "w1"))
        service = TurnService(workspace, store, self.factory, max_turns=24)
        before = _digests(workspace)
        started = time.monotonic()
        info = await service.accept(
            case.prompt,
            f"k-{case.key}",
            context=ContextRequest(
                selected_path=case.selected,
                reference_paths=case.references,
            )
            if case.selected or case.references
            else None,
            purpose=case.purpose,
            index_paths=case.index_paths,
        )
        await service.wait(info.turn_id)
        events = list(store.events(info.conversation_id))
        follow: list[dict[str, Any]] = []
        if case.follow_up:
            follow_info = await service.accept(
                case.follow_up,
                f"k-{case.key}-2",
                conversation_id=info.conversation_id,
            )
            await service.wait(follow_info.turn_id)
            follow = list(store.events(info.conversation_id))
        after = _digests(workspace)
        changed = sorted(
            path for path in set(before) | set(after) if before.get(path) != after.get(path)
        )
        checks = _checks(case, workspace, store, events, changed, follow)
        return {
            "key": case.key,
            "title": case.title,
            "model": self.model,
            "prompt": case.prompt,
            "follow_up": case.follow_up,
            "notes": case.notes,
            "duration_seconds": round(time.monotonic() - started, 1),
            "changed": changed,
            "checks": checks,
            "status": "pass" if all(item["ok"] for item in checks) else "fail",
            "events": _brief(events),
            "follow_events": _brief(follow),
            "index": _index_snapshot(store, workspace),
        }


def _seed(workspace: Workspace, path: str, text: str) -> None:
    """写入夹具内容：初始化时已存在的空设定要按已有指纹改写，不能声明为新建。"""
    try:
        existing = workspace.read(path)
    except WorkspaceError:
        workspace.write(path, text, expected_digest=None)
    else:
        workspace.write(path, text, expected_digest=existing.digest)


def _digests(workspace: Workspace) -> dict[str, str]:
    catalog = workspace.catalog()
    paths = [
        entry.path
        for group in (catalog.chapters, catalog.characters, catalog.settings)
        for entry in group
        if entry.exists
    ]
    return {path: workspace.read(path).digest for path in paths}


def _brief(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """只留下判断需要的事件字段，避免把整段正文写进验收材料。"""
    keep = (
        "turn_started",
        "context_prepared",
        "material_read",
        "task_scope_set",
        "task_scope_extended",
        "tool_finished",
        "chapter_index_updated",
        "review_requested",
        "review_finished",
        "run_finished",
        "error",
    )
    fields = {
        "task_scope_set": ("version", "mode", "summary", "grants", "denied_paths"),
        "task_scope_extended": ("version", "grants", "evidence_path", "evidence"),
        "tool_finished": ("tool", "ok", "summary"),
        "review_finished": ("status", "report_id", "findings", "uncovered"),
        "run_finished": ("status", "recording", "changed_paths"),
        "error": ("code", "message"),
        "context_prepared": ("selected_path", "references", "provided"),
        "material_read": ("path", "start", "end"),
        "chapter_index_updated": ("path", "status", "entries"),
    }
    rows: list[dict[str, Any]] = []
    for event in events:
        kind = str(event.get("type"))
        if kind not in keep:
            continue
        row: dict[str, Any] = {"type": kind}
        for name in fields.get(kind, ()):
            if name in event:
                value = event[name]
                if isinstance(value, str) and len(value) > 400:
                    value = value[:400] + "…"
                if kind == "tool_finished" and name == "summary" and isinstance(value, str):
                    value = value[:200]
                row[name] = value
        rows.append(row)
    return rows


def _scope_grants(events: list[dict[str, Any]]) -> set[str]:
    paths: set[str] = set()
    for event in events:
        if event.get("type") not in ("task_scope_set", "task_scope_extended"):
            continue
        for grant in event.get("grants") or []:
            if isinstance(grant, dict) and isinstance(grant.get("path"), str):
                paths.add(grant["path"])
    return paths


def _read_paths(events: list[dict[str, Any]]) -> set[str]:
    return {
        str(event.get("path"))
        for event in events
        if event.get("type") == "material_read" and event.get("path")
    }


def _finished(events: list[dict[str, Any]]) -> dict[str, Any]:
    for event in reversed(events):
        if event.get("type") == "run_finished":
            return event
    return {}


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _checks(
    case: Case,
    workspace: Workspace,
    store: ConversationStore,
    events: list[dict[str, Any]],
    changed: list[str],
    follow: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    finished = _finished(events)
    rows = [_check("整轮完成", finished.get("status") == "completed", str(finished))]
    for path in case.forbidden:
        rows.append(_check(f"未改动 {path}", path not in changed, f"changed={changed}"))
    if case.allowed_changes:
        outside = [path for path in changed if path not in case.allowed_changes]
        rows.append(_check("只改了允许的路径", not outside, f"越界={outside}"))
    if case.key == "refs_locate":
        read = _read_paths(events)
        rows.append(_check("读过第一章", "正文/0001 雨夜.md" in read, str(sorted(read))))
    if case.key == "reference_not_authority":
        prepared = next(
            (event for event in events if event.get("type") == "context_prepared"), {}
        )
        # references 是 {path, digest} 列表，判断只看路径。
        refs = [
            item.get("path") if isinstance(item, dict) else item
            for item in prepared.get("references") or []
        ]
        rows.append(_check("小传作为参考登记", CHARACTER in refs, str(refs)))
    if case.key in ("fixed_scope", "sync_chapters", "review_after_write"):
        grants = _scope_grants(events)
        rows.append(
            _check("写入路径都在已登记范围内", set(changed) <= grants, f"grants={sorted(grants)}")
        )
    if case.key == "sync_chapters":
        extended = [event for event in events if event.get("type") == "task_scope_extended"]
        read = _read_paths(events)
        # 追加是可发现范围的一条路，不是唯一做法：一次登记完整同样合规。这里只在真的
        # 追加时核对依据，同时把「这次走没走追加」记进报告，避免把未覆盖说成已覆盖。
        rows.append(
            _check(
                "追加时依据带读过文件的原文",
                all(
                    event.get("evidence_path")
                    and event.get("evidence")
                    and event.get("evidence_path") in read
                    for event in extended
                ),
                str([(e.get("evidence_path"), str(e.get("evidence"))[:40]) for e in extended])
                or "本次没有追加",
            )
        )
        rows.append(
            _check(
                "范围变更可在事件里看到",
                bool(extended) or any(e.get("type") == "task_scope_set" for e in events),
                f"extended={len(extended)}",
            )
        )
    if case.key == "discuss_only":
        rows.append(_check("讨论不写文件", not changed, f"changed={changed}"))
    if case.key == "index_recall":
        indexed = [event for event in events if event.get("type") == "chapter_index_updated"]
        rows.append(_check("生成章节索引", bool(indexed), str(indexed[:1])))
        allowed = _scope_grants(events) | _scope_grants(follow)
        rows.append(_check("后续写作都在已登记范围内", set(changed) <= allowed, str(changed)))
    if case.key == "review_after_write":
        review = next((e for e in events if e.get("type") == "review_finished"), {})
        rows.append(_check("形成检查报告", bool(review), str(review)[:300]))
        if review:
            rows.append(
                _check(
                    "报告给出结论或未覆盖项",
                    review.get("status") in ("findings", "no_findings", "incomplete")
                    and (review.get("status") != "incomplete" or bool(review.get("uncovered"))),
                    f"status={review.get('status')}",
                )
            )
            for finding in review.get("findings") or []:
                for evidence in finding.get("evidence") or []:
                    path = str(evidence.get("path"))
                    rows.append(
                        _check(
                            f"证据来自本轮读过的文件：{path}",
                            path in _read_paths(events),
                            str(sorted(_read_paths(events))),
                        )
                    )
    return rows


def _index_snapshot(store: ConversationStore, workspace: Workspace) -> list[dict[str, Any]]:
    records = ChapterIndexStore(store.root).records(workspace)
    return [
        {
            "path": record.source_path,
            "status": record.status,
            "digest": record.source_digest,
            "entries": len(record.entries()),
        }
        for record in records
    ]


async def _run_all(
    cases: tuple[Case, ...], work: Path, out: Path, model: str | None
) -> int:
    config_path = DEFAULT_CONFIG_PATH
    book, _profile, boot_model = load_provider_book(
        config_path, model, build_credential_store()
    )
    factory = configured_provider_factory(book)
    runner = Runner(work, factory, boot_model)
    results = []
    for case in cases:
        print(f"[case] {case.key} …", flush=True)
        results.append(await runner.run(case))
        print(f"[case] {case.key} -> {results[-1]['status']}", flush=True)
    _write(out, results, boot_model)
    failed = [item["key"] for item in results if item["status"] == "fail"]
    print(f"完成 {len(results)} 项，失败：{failed or '无'}", flush=True)
    return 0 if not failed else 1


def _write(out: Path, results: list[dict[str, Any]], model: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(
        json.dumps({"model": model, "results": results}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [f"# 七类任务验收（模型：{model}）", ""]
    for item in results:
        lines.append(f"## {item['title']}（`{item['key']}`，{item['status']}）")
        lines.append("")
        lines.append(f"- 提示：{item['prompt']}")
        if item["follow_up"]:
            lines.append(f"- 后续：{item['follow_up']}")
        lines.append(f"- 改动：{'、'.join(item['changed']) or '无'}")
        lines.append(f"- 耗时：{item['duration_seconds']} 秒")
        for check in item["checks"]:
            mark = "通过" if check["ok"] else "不通过"
            lines.append(f"- {mark}：{check['name']}（{check['detail']}）")
        lines.append("")
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="dev-notes/v0.6/artifacts/model-acceptance")
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--model", default=None, help="覆盖模型名；默认用 config.yaml 的选择")
    args = parser.parse_args()
    selected = tuple(
        case for case in CASES if not args.case or case.key in set(args.case)
    )
    if not selected:
        parser.error("没有匹配的用例")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = Path(args.out) / stamp
    # 作品与状态目录放数据目录：验收材料只收报告，不收整部作品。
    work = Path("data/test-tmp/model-acceptance") / stamp
    return asyncio.run(_run_all(selected, work, out, args.model))


if __name__ == "__main__":
    raise SystemExit(main())

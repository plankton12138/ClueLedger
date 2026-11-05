"""用真模型跑 v1.3 线索注入样例（花费受预算硬约束）。

每个样例装配成一本迷你作品（结构、前章正文、章级登记、目标章骨架），再走产品
真实的起草链路（``CreativeAssistService``：注入装配 → 模型调用 → 自查四层解析），
最后按四类结果打分：

1. **注入**（硬）：提示词里该出现的线索段/发现项在不在，已收线索有没有占上下文；
2. **自查四层**（硬）：模型有没有按 P2 的新模板产出第 4 层线索核对；
3. **正文落实**（观察）：模型是否把线索写进正文（关键词命中）；
4. **线索核对**（观察）：自查第 4 层是否点到对应编号。

硬项失败按产品口径算失败；观察项只记录，不当作缺陷——模型行为有概率性，探针
的职责是把「注入了什么、模型怎么反应」如实留证。

用法：

    uv run python scripts/clue_model_probe.py --dry-run          # 不调模型，只看装配
    uv run python scripts/clue_model_probe.py --budget 2.5        # 默认就是 2.5 元
    uv run python scripts/clue_model_probe.py --case plant_ch1 --repeat 2

费用：按 token 用量乘单价估算（默认输入 2 元/百万、缓存命中 0.5 元/百万、
输出 8 元/百万，接近 DeepSeek 公开价）。每跑完一个样例累计一次，累计达到
``--budget`` 就停，剩余样例标为未运行。报告里的费用是估算值，token 数是厂商
回传的准确值。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import tempfile
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from clue_probe_samples import SAMPLES, Sample, SeedClue

from hastings.infrastructure.credentials import build_credential_store
from hastings.prompts.self_check import split_self_check
from hastings.server.__main__ import DEFAULT_CONFIG_PATH, load_provider_book
from hastings.server.file_app import configured_provider_factory
from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.chapter_clues import ClueEntry, ResolvesRef, anchor_paragraph
from hastings.workspace.chapter_writing import save_chapter_prose
from hastings.workspace.clue_proposals import locate_paragraph, split_clue_proposals
from hastings.workspace.creative_assist import CreativeAssistInput, CreativeAssistService
from hastings.workspace.creative_candidates import (
    CandidateBase,
    CreativeCandidateStore,
    CreativeTarget,
)
from hastings.workspace.files import Workspace
from hastings.workspace.state import conversation_root
from hastings.workspace.story_models import FORMAT_VERSION
from hastings.workspace.story_store import EMPTY_REVISION, MAIN_VARIANT_ID, StoryStore
from hastings.workspace.writing_actions import ChapterCreateResult, create_chapter
from tau_agent.messages import Usage
from tau_ai.events import AssistantDoneEvent

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "dev-notes" / "v1.3" / "artifacts"


# ---------------------------------------------------------------------------
# 用量与预算
# ---------------------------------------------------------------------------


@dataclass
class Meter:
    """按 token 累计用量与估算费用（人民币元）。"""

    price_in: float = 2.0
    price_cache: float = 0.5
    price_out: float = 8.0
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    reasoning: int = 0
    calls: int = 0
    cost_rmb: float = 0.0

    def add(self, usage: Usage) -> None:
        self.calls += 1
        self.input += usage.input
        self.output += usage.output
        self.cache_read += usage.cache_read
        self.cache_write += usage.cache_write
        self.reasoning += usage.reasoning or 0
        self.cost_rmb += (
            usage.input * self.price_in
            + usage.output * self.price_out
            + usage.cache_read * self.price_cache
            # 缓存写入按输入价估，不额外加价——宁可高估，不低估。
            + usage.cache_write * self.price_in
        ) / 1_000_000

    def snapshot(self) -> dict[str, float | int]:
        return {
            "calls": self.calls,
            "input": self.input,
            "output": self.output,
            "cache_read": self.cache_read,
            "cache_write": self.cache_write,
            "reasoning": self.reasoning,
            "cost_rmb": round(self.cost_rmb, 4),
        }


class _MeteredProvider:
    """包一层 provider，只观测用量；其余行为原样透传。"""

    def __init__(self, inner: Any, meter: Meter) -> None:
        self._inner = inner
        self._meter = meter

    async def stream_response(self, **kwargs: Any) -> AsyncIterator[Any]:
        async for event in self._inner.stream_response(**kwargs):
            if isinstance(event, AssistantDoneEvent):
                self._meter.add(event.message.usage)
            yield event

    async def aclose(self) -> None:
        close = getattr(self._inner, "aclose", None)
        if close is not None:
            await close()


def metered_factory(book: Any, meter: Meter) -> Any:
    base = configured_provider_factory(book)

    def factory() -> ModelSnapshot:
        snap = base()
        return ModelSnapshot(
            connection_id=snap.connection_id,
            display_name=snap.display_name,
            model=snap.model,
            provider=cast(Any, _MeteredProvider(snap.provider, meter)),
            thinking_level=snap.thinking_level,
            limits=snap.limits,
        )

    return factory


# ---------------------------------------------------------------------------
# 作品装配
# ---------------------------------------------------------------------------


@dataclass
class BookSetup:
    root: Path
    state_root: Path
    workspace: Workspace
    store: StoryStore
    chapters: list[ChapterCreateResult]
    target: ChapterCreateResult
    sample: Sample

    @property
    def target_number(self) -> int:
        return self.sample.target_index + 1


def _paragraph_with(text: str, snippet: str) -> str:
    for paragraph in text.split("\n\n"):
        if snippet in paragraph:
            return paragraph.strip()
    raise ValueError(f"样例正文里找不到锚段：{snippet!r}")


def _entry(seed: SeedClue, text: str) -> ClueEntry:
    anchor = None
    if seed.anchor_text is not None:
        anchor = anchor_paragraph(_paragraph_with(text, seed.anchor_text), "probe")
    resolves = (
        ResolvesRef(clue_id=seed.resolves[0], planted_in=seed.resolves[1])
        if seed.resolves is not None
        else None
    )
    return ClueEntry(
        id=seed.id,
        kind=seed.kind,  # type: ignore[arg-type]
        status=seed.status,  # type: ignore[arg-type]
        origin=seed.origin,  # type: ignore[arg-type]
        note=seed.note,
        anchor=anchor,
        resolves=resolves,
    )


def build_book(sample: Sample, base: Path) -> BookSetup:
    """按样例真实落盘：建章 → 存正文（带线索登记）→ 需要时改稿制造失联。"""
    root = base / "作品"
    state = base / "state"
    root.mkdir(parents=True, exist_ok=False)
    workspace = Workspace.open(root)
    state_root = conversation_root(state, "probe")
    store = StoryStore(state_root, workspace, format_version=FORMAT_VERSION)

    created: list[ChapterCreateResult] = []
    previous: str | None = None
    for index, seed in enumerate(sample.chapters, start=1):
        # 每章现读修订号：存正文本身也会推进作品修订，不能沿用上一章的返回值。
        variant = store.read().variant(MAIN_VARIANT_ID)
        result = create_chapter(
            store,
            workspace,
            title=seed.title,
            after_chapter_id=previous,
            expected_plan_revision=variant.revision if variant is not None else EMPTY_REVISION,
            request_key=f"{sample.key}:create:{index}",
        )
        previous = result.chapter_id
        created.append(result)
        if not seed.text:
            continue
        entries = [_entry(clue, seed.text) for clue in seed.clues]
        save_chapter_prose(
            store,
            workspace,
            chapter_id=result.chapter_id,
            text=seed.text,
            expected_digest=result.prose_digest,
            request_key=f"{sample.key}:prose:{index}",
            draft_revision="probe",
            clue_entries=entries or None,
        )
        if seed.rewrite is not None:
            view = workspace.read(result.path)
            save_chapter_prose(
                store,
                workspace,
                chapter_id=result.chapter_id,
                text=seed.rewrite,
                expected_digest=view.digest,
                request_key=f"{sample.key}:rewrite:{index}",
                draft_revision="probe",
                clue_entries=None,
            )
    # 结构文件最后写：StoryStore 首次读取会做遗留结构迁移，先放 STRUCTURE.md
    # 会让首章建章拿到非空的计划修订号。
    (root / "STRUCTURE.md").write_text(sample.structure, encoding="utf-8")
    return BookSetup(
        root=root,
        state_root=state_root,
        workspace=workspace,
        store=store,
        chapters=created,
        target=created[sample.target_index],
        sample=sample,
    )


def _request(setup: BookSetup) -> CreativeAssistInput:
    return CreativeAssistInput(
        kind="prose_draft",
        target=CreativeTarget(kind="prose", chapter_id=setup.target.chapter_id),
        base=CandidateBase(
            plan_revision=setup.target.plan_revision,
            prose_digest=setup.target.prose_digest,
        ),
        author_note=setup.sample.author_note,
        request_key=f"{setup.sample.key}:draft",
    )


def _service(setup: BookSetup, factory: Any) -> CreativeAssistService:
    return CreativeAssistService(
        state_root=setup.state_root,
        workspace=setup.workspace,
        candidates=CreativeCandidateStore(setup.state_root),
        provider_factory=factory,
        workspace_id="probe",
    )


def build_prompt(setup: BookSetup) -> tuple[str, str]:
    """只装配提示词，不调模型（干跑与真实运行共用同一条路径）。"""
    service = _service(setup, lambda: (_ for _ in ()).throw(RuntimeError("dry-run")))
    request = _request(setup)
    variant, plan, _, _ = service._resolve(request)
    return service._prompt(request, variant, plan)


# ---------------------------------------------------------------------------
# 打分
# ---------------------------------------------------------------------------


def _missing(text: str, needles: tuple[str, ...]) -> list[str]:
    return [needle for needle in needles if needle not in text]


def score(sample: Sample, prompt: str, output: str) -> dict[str, Any]:
    # 登记小节先切出去，免得条目行混进自查第 4 层（与产品解析同序）。
    body, proposals, rejects = split_clue_proposals(output)
    block = split_self_check(body)
    clue_text = "\n".join(block.clue_items)
    anchored = sum(1 for item in proposals if locate_paragraph(item.quote, block.prose) is not None)
    prose_miss = [
        list(group)
        for group in sample.expect_prose
        if not any(word in block.prose for word in group)
    ]
    mentions = [clue_id for clue_id in sample.expect_clue_mentions if clue_id not in clue_text]
    return {
        "prompt_missing": _missing(prompt, sample.expect_prompt),
        "prompt_leaks": [text for text in sample.forbid_prompt if text in prompt],
        "self_check_present": block.present,
        "self_check_missing_layers": list(block.missing_layers),
        "self_check_missing_terms": _missing(clue_text, sample.expect_self_check),
        "prose_missing_groups": prose_miss,
        "clue_mentions_missing": mentions,
        "proposal_count": len(proposals),
        "proposal_missing": max(0, sample.expect_proposals - len(proposals)),
        "proposal_rejects": rejects,
        "proposal_anchored": anchored,
        "proposal_unanchored": bool(
            sample.expect_proposals_anchored and proposals and not anchored
        ),
    }


def verdict(row: dict[str, Any]) -> str:
    if row.get("skipped"):
        return "未运行"
    hard = (
        not row["score"]["prompt_missing"]
        and not row["score"]["prompt_leaks"]
        and row["score"]["self_check_present"]
        and not row["score"]["self_check_missing_terms"]
        and not row["score"].get("proposal_missing")
        and not row["score"].get("proposal_unanchored")
    )
    soft = not row["score"]["prose_missing_groups"] and not row["score"]["clue_mentions_missing"]
    if not hard:
        return "失败"
    return "通过" if soft else "通过（观察项未命中）"


# ---------------------------------------------------------------------------
# 运行
# ---------------------------------------------------------------------------


@dataclass
class CaseResult:
    key: str
    title: str
    note: str
    prompt: str
    output: str = ""
    self_check: dict[str, Any] | None = None
    score: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, float | int] = field(default_factory=dict)
    status: str = ""
    error: str | None = None
    skipped: bool = False
    seconds: float = 0.0


async def _draft(
    setup: BookSetup,
    service: CreativeAssistService,
    timeout: float,
) -> tuple[str, str | None]:
    request = _request(setup)
    candidate = await service.start(request)
    deadline = time.monotonic() + timeout
    while candidate.status == "streaming":
        if time.monotonic() > deadline:
            await service.stop(candidate.candidate_id)
            return candidate.text, f"超时（{timeout:.0f}s）"
        await asyncio.sleep(0.25)
        candidate = service.candidates.read(candidate.candidate_id)
    if candidate.status != "ready":
        return candidate.text, candidate.error or f"候选状态 {candidate.status}"
    return candidate.text, None


async def run_case(
    sample: Sample,
    base: Path,
    factory: Any,
    meter: Meter,
    budget: float,
    timeout: float,
) -> CaseResult:
    result = CaseResult(key=sample.key, title=sample.title, note=sample.note, prompt="")
    if meter.cost_rmb >= budget:
        result.skipped = True
        result.status = "未运行（预算用尽）"
        return result
    started = time.monotonic()
    try:
        setup = build_book(sample, base / sample.key)
    except Exception as exc:  # 装配失败是探针自己的问题，如实记录
        result.status = "装配失败"
        result.error = f"{type(exc).__name__}: {exc}"
        return result
    _, prompt = build_prompt(setup)
    result.prompt = prompt
    service = _service(setup, factory)
    try:
        output, error = await _draft(setup, service, timeout)
    except Exception as exc:
        output, error = "", f"{type(exc).__name__}: {exc}"
    result.output = output
    result.error = error
    result.seconds = round(time.monotonic() - started, 1)
    result.score = score(sample, prompt, output)
    result.status = "失败" if error else "完成"
    return result


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------


#: 提示词里紧跟在注入段之后的分区头；截取注入段时遇到它们就停。
_SECTION_ENDS = ("\n### ", "\n本章已入账信息：")


def _section(prompt: str, header: str) -> str:
    """从提示词里截出「标题 + 其下所有行」，到下一个分区头为止。"""
    marker = prompt.find(header)
    if marker < 0:
        return ""
    end = len(prompt)
    for boundary in _SECTION_ENDS:
        found = prompt.find(boundary, marker + len(header))
        if found >= 0:
            end = min(end, found)
    return prompt[marker:end].strip()


def render_report(results: list[CaseResult], meter: Meter, budget: float, model: str) -> str:
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# v1.3 线索注入真模型探针",
        "",
        f"- 时间：{stamp}",
        f"- 模型：{model}",
        f"- 预算：{budget:.2f} 元｜已用（估算）：{meter.cost_rmb:.2f} 元",
        f"- 调用：{meter.calls} 次｜输入 {meter.input}｜输出 {meter.output}｜"
        f"缓存命中 {meter.cache_read}｜推理 {meter.reasoning}",
        "",
        "## 汇总",
        "",
        "| 样例 | 结论 | 注入 | 自查四层 | 正文落实 | 线索核对 | 输出 tokens | 估计费用 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in results:
        if row.skipped:
            lines.append(f"| {row.key} | 未运行 | — | — | — | — | — | — |")
            continue
        sc = row.score
        inject = "通过" if not sc["prompt_missing"] and not sc["prompt_leaks"] else "失败"
        layers = "通过" if sc["self_check_present"] else "缺层"
        prose = "命中" if not sc["prose_missing_groups"] else "未命中"
        clues = "命中" if not sc["clue_mentions_missing"] else "未命中"
        usage = row.usage
        lines.append(
            f"| {row.key} | {verdict(_row_dict(row))} | {inject} | {layers} | {prose} | "
            f"{clues} | {usage.get('output', 0)} | {usage.get('cost_rmb', 0):.3f} 元 |"
        )
    for row in results:
        lines.extend(["", f"## {row.key}｜{row.title}", "", row.note, ""])
        if row.skipped:
            lines.append("未运行：预算已用尽。")
            continue
        if row.error:
            lines.extend([f"**错误**：`{row.error}`", ""])
        sc = row.score
        lines.append(f"- 结论：**{verdict(_row_dict(row))}**，耗时 {row.seconds}s")
        lines.append(f"- 提示词长度：{len(row.prompt)} 字｜输出长度：{len(row.output)} 字")
        if sc["prompt_missing"]:
            lines.append(f"- 注入缺失：{'、'.join(sc['prompt_missing'])}")
        if sc["prompt_leaks"]:
            lines.append(f"- 不应出现却出现：{'、'.join(sc['prompt_leaks'])}")
        if sc["self_check_missing_layers"]:
            lines.append(f"- 自查缺层：{'、'.join(sc['self_check_missing_layers'])}")
        if sc["self_check_missing_terms"]:
            lines.append(f"- 线索核对缺少：「{'」「'.join(sc['self_check_missing_terms'])}」")
        if sc["prose_missing_groups"]:
            groups = "；".join("/".join(group) for group in sc["prose_missing_groups"])
            lines.append(f"- 正文未命中：{groups}")
        if sc["clue_mentions_missing"]:
            lines.append(f"- 自查未提到：{'、'.join(sc['clue_mentions_missing'])}")
        clue_block = _section(row.prompt, "### 线索清单")
        report_block = _section(row.prompt, "### 线索对账")
        if report_block:
            lines.extend(["", "注入的对账报告：", "", "```text", report_block, "```"])
        if clue_block:
            lines.extend(["", "注入的线索清单：", "", "```text", clue_block, "```"])
        clue_items = (row.self_check or {}).get("clue_items") or []
        if clue_items:
            lines.extend(["", f"自查第 4 层：{' / '.join(str(item) for item in clue_items)}"])
    lines.append("")
    return "\n".join(lines)


def _row_dict(row: CaseResult) -> dict[str, Any]:
    return {"skipped": row.skipped, "score": row.score}


def write_artifacts(
    out_dir: Path, results: list[CaseResult], meter: Meter, budget: float, model: str
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    for row in results:
        case_dir = out_dir / "cases" / row.key
        case_dir.mkdir(parents=True, exist_ok=True)
        if row.prompt:
            (case_dir / "prompt.md").write_text(row.prompt, encoding="utf-8")
        if row.output:
            (case_dir / "output.md").write_text(row.output, encoding="utf-8")
        (case_dir / "result.json").write_text(
            json.dumps(
                {
                    "key": row.key,
                    "title": row.title,
                    "note": row.note,
                    "status": row.status,
                    "error": row.error,
                    "seconds": row.seconds,
                    "usage": row.usage,
                    "score": row.score,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    report = render_report(results, meter, budget, model)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    (out_dir / "report.json").write_text(
        json.dumps(
            {
                "model": model,
                "budget_rmb": budget,
                "usage": meter.snapshot(),
                "cases": [
                    {
                        "key": row.key,
                        "status": row.status,
                        "error": row.error,
                        "usage": row.usage,
                        "score": row.score,
                    }
                    for row in results
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return out_dir


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clue-model-probe", description="用真模型跑 v1.3 线索注入样例（受预算约束）"
    )
    parser.add_argument("--case", action="append", default=[], help="只跑指定样例，可重复")
    parser.add_argument("--repeat", type=int, default=1, help="每个样例重复次数")
    parser.add_argument("--budget", type=float, default=2.5, help="估算费用上限（元）")
    parser.add_argument("--price-in", type=float, default=2.0, help="输入价（元/百万 token）")
    parser.add_argument(
        "--price-cache", type=float, default=0.5, help="缓存命中价（元/百万 token）"
    )
    parser.add_argument("--price-out", type=float, default=8.0, help="输出价（元/百万 token）")
    parser.add_argument("--timeout", type=float, default=300.0, help="单个样例超时（秒）")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="模型来源配置")
    parser.add_argument("--model", default="", help="覆盖模型名")
    parser.add_argument("--dry-run", action="store_true", help="只装配并打印提示词，不调模型")
    parser.add_argument(
        "--keep-books", action="store_true", help="保留临时作品目录（默认跑完删除）"
    )
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help="报告目录（其下新建时间戳子目录）"
    )
    return parser


def select_samples(keys: list[str]) -> list[Sample]:
    if not keys:
        return list(SAMPLES)
    wanted = set(keys)
    known = {sample.key for sample in SAMPLES}
    unknown = wanted - known
    if unknown:
        raise SystemExit(
            f"未知样例：{'、'.join(sorted(unknown))}；可选：{'、'.join(sorted(known))}"
        )
    return [sample for sample in SAMPLES if sample.key in wanted]


def dry_run(samples: list[Sample]) -> int:
    failed = False
    scratch = Path(tempfile.mkdtemp(prefix="clue-probe-dry-"))
    try:
        for sample in samples:
            setup = build_book(sample, scratch / sample.key)
            _, prompt = build_prompt(setup)
            result = score(sample, prompt, "")
            problems: list[str] = []
            if result["prompt_missing"]:
                problems.append(f"缺注入：{'、'.join(result['prompt_missing'])}")
            if result["prompt_leaks"]:
                problems.append(f"多余注入：{'、'.join(result['prompt_leaks'])}")
            print(
                f"[dry] {sample.key}：提示词 {len(prompt)} 字"
                + ("；" + "；".join(problems) if problems else "；注入断言通过")
            )
            for header in ("### 线索对账", "### 线索清单"):
                section = _section(prompt, header)
                if section:
                    print("   " + section.replace("\n", "\n   "))
            failed = failed or bool(problems)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return 0 if not failed else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    samples = select_samples(args.case)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    work = args.out / f"clue-probe-{stamp}"

    if args.dry_run:
        return dry_run(samples)

    book, _, _ = load_provider_book(args.config, args.model or None, build_credential_store())
    selection = book.selection()
    if not selection.available:
        raise SystemExit(f"没有可用的模型连接：{selection.reason or '先在设置里完成连接与选择'}")
    meter = Meter(price_in=args.price_in, price_cache=args.price_cache, price_out=args.price_out)
    factory = metered_factory(book, meter)
    books = work if args.keep_books else Path(tempfile.mkdtemp(prefix="clue-probe-books-"))
    results: list[CaseResult] = []
    for sample in samples:
        for run in range(1, args.repeat + 1):
            key = sample.key if args.repeat == 1 else f"{sample.key}--{run}"
            before = meter.snapshot()
            print(f"[probe] {key} …", end="", flush=True)
            row = asyncio.run(
                run_case(
                    sample,
                    books / f"run{run}" if args.repeat > 1 else books,
                    factory,
                    meter,
                    args.budget,
                    args.timeout,
                )
            )
            after = meter.snapshot()
            row.usage = {
                field_name: after[field_name] - before[field_name]  # type: ignore[operator]
                for field_name in ("input", "output", "cache_read", "reasoning", "cost_rmb")
            }
            row.key = key
            print(
                f" {verdict(_row_dict(row))}｜{row.usage['output']} 输出 token｜"
                f"累计 {meter.cost_rmb:.3f} 元"
            )
            results.append(row)
            if meter.cost_rmb >= args.budget:
                print(
                    f"[probe] 预算用尽（估算 {meter.cost_rmb:.2f} 元 ≥ {args.budget:.2f} 元），停止"
                )
                break
    out = write_artifacts(work, results, meter, args.budget, selection.model)
    if not args.keep_books:
        shutil.rmtree(books, ignore_errors=True)
    print(f"报告：{out / 'report.md'}")
    print(
        f"合计：{meter.calls} 次调用，输入 {meter.input}、输出 {meter.output}，"
        f"估算 {meter.cost_rmb:.3f} 元"
    )
    hard_fail = any(row.status == "失败" or row.status == "装配失败" for row in results)
    return 0 if not hard_fail else 1


if __name__ == "__main__":
    raise SystemExit(main())

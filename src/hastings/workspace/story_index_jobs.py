"""章节增量语义索引任务（1.0.9 P3）。

正文保存成功后，只把变化段落、本章构思和直接相关信息线交给模型；结果只产生
「正文新增」或「需要对齐」候选，不修改正式构思，也不冒充正文事实。每章同一时刻只有
一个任务；新保存取消或作废旧任务，基础摘要不匹配的结果标为 ``stale``。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable
from contextlib import aclosing
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.atomic_io import atomic_write_json, locked
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.story_models import FORMAT_VERSION, ChapterPlan, StoryVariant
from hastings.workspace.story_store import MAIN_VARIANT_ID, StoryStore, new_id
from tau_agent.messages import UserMessage, message_text
from tau_agent.provider_events import AssistantDoneEvent, AssistantErrorEvent, TextDeltaEvent

__all__ = [
    "IndexAnalysisService",
    "IndexJob",
    "IndexJobCandidate",
    "IndexJobStore",
    "changed_text",
]

JobStatus = Literal["queued", "running", "ready", "failed", "unavailable", "stale"]


class IndexJobCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    thread_id: str | None = None
    move_id: str | None = None
    label: str
    statement: str
    quote: str | None = None
    kind: Literal["observed", "alignment"] = "observed"
    explanation: str | None = None


class IndexJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    chapter_id: str
    status: JobStatus = "queued"
    based_on_digest: str
    context_revision: str
    plan_revision: str
    changed_text: str = ""
    candidates: list[IndexJobCandidate] = Field(default_factory=list)
    error: str | None = None
    created_at: str
    updated_at: str


class IndexJobStore:
    """每章一条最新任务；旧任务被新保存覆盖，不保留历史执行记录。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.dir = self.root / "writing" / "index_jobs"
        self.lock_file = self.dir / ".lock"

    def read(self, chapter_id: str) -> IndexJob | None:
        path = self._path(chapter_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return None
        try:
            job = IndexJob.model_validate(payload)
        except ValueError:
            return None
        if job.status in ("queued", "running") and job.chapter_id not in _RUNNING:
            job.status = "failed"
            job.error = "分析在进程重启后中断，请重新保存正文触发索引"
            self.save(job)
        return job

    def save(self, job: IndexJob) -> None:
        with locked(self.lock_file):
            atomic_write_json(
                self._path(job.chapter_id), job.model_dump(mode="json"), prefix=".job-"
            )

    def _path(self, chapter_id: str) -> Path:
        if not chapter_id or "/" in chapter_id or "\\" in chapter_id:
            raise WorkspaceError(
                code="STORY_INDEX_FAILED",
                where="index-job",
                why="章节标识不合法",
                fix="刷新工作台后重新保存正文",
            )
        return self.dir / f"{chapter_id}.json"


_RUNNING: dict[str, asyncio.Task[None]] = {}


class IndexAnalysisService:
    def __init__(
        self,
        *,
        state_root: Path,
        workspace: Workspace,
        provider_factory: Callable[[], ModelSnapshot],
    ) -> None:
        self.state_root = state_root
        self.workspace = workspace
        self.provider_factory = provider_factory
        self.jobs = IndexJobStore(state_root)

    async def enqueue(
        self,
        *,
        variant: StoryVariant,
        plan: ChapterPlan,
        prose_digest: str,
        context_revision: str,
        changed: str,
    ) -> str | None:
        previous = _RUNNING.pop(plan.id, None)
        if previous is not None:
            previous.cancel()
        if not changed.strip():
            return None
        now = _now()
        job = IndexJob(
            job_id=new_id("job"),
            chapter_id=plan.id,
            based_on_digest=prose_digest,
            context_revision=context_revision,
            plan_revision=variant.revision,
            changed_text=changed[:3000],
            created_at=now,
            updated_at=now,
        )
        try:
            snapshot = self.provider_factory()
        except Exception:
            job.status = "unavailable"
            job.error = "没有可用模型"
            self.jobs.save(job)
            return None
        task = asyncio.create_task(self._run(job, snapshot, variant, plan))
        _RUNNING[plan.id] = task
        task.add_done_callback(lambda _: _RUNNING.pop(plan.id, None))
        self.jobs.save(job)
        return job.job_id

    async def _run(
        self,
        job: IndexJob,
        snapshot: ModelSnapshot,
        variant: StoryVariant,
        plan: ChapterPlan,
    ) -> None:
        job.status = "running"
        self._save_if_current(job)
        text = ""
        final: str | None = None
        try:
            system = (
                "你是悬疑小说信息索引助手。只输出 JSON 行，不解释。"
                '每行格式：{"statement":"正文新增或需要对齐的信息","quote":"准确原句（可省略）",'
                '"kind":"observed|alignment","thread_id":"已有信息线标识（可省略）"}'
            )
            quotes = {
                binding.move_id: binding.quote
                for binding in variant.bindings.values()
                if binding.move_id is not None and binding.path == plan.prose_path
            }
            moves = "\n".join(
                f"- {move.id}｜{move.statement}"
                + (f"｜原句：{quotes[move.id]}" if move.id in quotes else "")
                for move in variant.information_moves_for_chapter(plan.id)
            )
            intent = variant.book_intent.text if variant.book_intent else ""
            prompt = (
                f"全书构思：{intent or '（未写）'}\n已入账信息：\n{moves or '（无）'}\n"
                f"本次变化段落：\n{job.changed_text}\n"
                "如果正文与某条已入账信息指向同一处细节但说法不一致（颜色、人物、时间、动作"
                "等被改写），输出 kind=alignment，并在 statement 里写清两边的内容；"
                "不要把它只当成正文新增。没有对应关系的新细节才用 observed。"
                "不要修改正式内容，不要推测作者真相。"
            )
            async with aclosing(
                snapshot.provider.stream_response(
                    model=snapshot.model,
                    system=system,
                    messages=[UserMessage(content=prompt)],
                    tools=[],
                    signal=None,
                )
            ) as stream:
                async for event in stream:
                    if isinstance(event, TextDeltaEvent):
                        text += event.delta
                    elif isinstance(event, AssistantErrorEvent):
                        raise WorkspaceError(
                            code="STORY_INDEX_FAILED",
                            where="story.index",
                            why=event.error.error_message or "索引请求未完成",
                            fix="正文已保存；稍后重试索引",
                        )
                    elif isinstance(event, AssistantDoneEvent):
                        if event.reason != "stop":
                            raise WorkspaceError(
                                code="STORY_INDEX_FAILED",
                                where="story.index",
                                why="索引请求没有完整结束",
                                fix="正文已保存；稍后重试索引",
                            )
                        final = message_text(event.message) or final
            if final:
                text = final
            current = (
                StoryStore(self.state_root, self.workspace, format_version=FORMAT_VERSION)
                .read()
                .variant(MAIN_VARIANT_ID)
            )
            current_plan = current.chapter(plan.id) if current is not None else None
            current_digest = ""
            if current_plan is not None and current_plan.prose_path is not None:
                try:
                    current_digest = self.workspace.read(current_plan.prose_path).digest
                except WorkspaceError:
                    current_digest = ""
            if (
                current is None
                or current.revision != job.plan_revision
                or (current_digest != job.based_on_digest)
            ):
                job.status = "stale"
                job.error = "正文或构思在分析期间已经变化"
            else:
                job.candidates = _parse_candidates(text, job.chapter_id)
                job.status = "ready"
                job.error = None
            job.updated_at = _now()
            self._save_if_current(job)
        except asyncio.CancelledError:
            job.status = "stale"
            job.error = "有新的保存请求，旧分析已取消"
            job.updated_at = _now()
            self._save_if_current(job)
            return
        except WorkspaceError as exc:
            job.status = "failed"
            job.error = f"[{exc.code}] {exc.why}"
            job.updated_at = _now()
            self._save_if_current(job)
        except Exception as exc:
            job.status = "failed"
            job.error = f"索引失败：{exc}"
            job.updated_at = _now()
            self._save_if_current(job)
        finally:
            await snapshot.aclose()

    def _save_if_current(self, job: IndexJob) -> None:
        """旧任务不能覆盖同一章已经排队的新任务。"""
        current = self.jobs.read(job.chapter_id)
        if current is not None and current.job_id != job.job_id:
            return
        self.jobs.save(job)


def _parse_candidates(text: str, chapter_id: str) -> list[IndexJobCandidate]:
    """只接受逐行 JSON；无法解析的行被忽略，不能把整段自由文本当候选。"""
    rows: list[IndexJobCandidate] = []
    for line in text.splitlines():
        raw = line.strip().strip("`")
        raw = re.sub(r"^json\s*", "", raw, flags=re.IGNORECASE)
        if not raw.startswith("{"):
            continue
        try:
            payload = json.loads(raw)
        except ValueError:
            continue
        statement = str(payload.get("statement") or "").strip()
        if not statement:
            continue
        quote = str(payload.get("quote") or "").strip() or None
        kind = payload.get("kind")
        rows.append(
            IndexJobCandidate(
                item_id=f"{chapter_id}:{len(rows)}",
                thread_id=str(payload.get("thread_id") or "") or None,
                label=statement[:200],
                statement=statement[:4000],
                quote=quote[:2000] if quote else None,
                kind="alignment" if kind == "alignment" else "observed",
                explanation=None,
            )
        )
    return rows


def changed_text(before: str, after: str, *, limit: int = 3000) -> str:
    """取变化段落而非整章；用行级差集，保持顺序。"""
    import difflib

    matcher = difflib.SequenceMatcher(None, before.splitlines(), after.splitlines())
    pieces: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag in {"replace", "delete"} and i1 < i2:
            pieces.append("（删除/改写前）" + "\n".join(before.splitlines()[i1:i2]))
        if tag in {"replace", "insert"} and j1 < j2:
            pieces.append("（当前正文）" + "\n".join(after.splitlines()[j1:j2]))
    merged = "\n".join(pieces)
    return merged[:limit]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")

"""正文导入预览与确认（V03-2.3 / 计划 M06）。

两条独立用例，**预览不写正文**：

1. ``create_preview``：解码 → 解析 → 上限校验 → 与现有章做内容去重 → 持久化
   预览（24 小时）。返回实际会被写入的文本、顺序、指纹与重复提示。
2. ``confirm``：校验预览版本/哈希与重复处理决定，把整批新增与显式替换合成
   一个领域命令，经 ``PublishManuscriptChange`` 一次提交——失败无半份正文。

关键取舍：

- **预览持久化在仓库辅助表**（bucket ``import_preview``），与正文同一存储；
  写入走「只有 aux_writes 的 CommitRequest」，不产生正文版本（引擎 no-op 分支）。
- **章身份在确认时由预览 ID + 序号确定性派生**：重试落回同一批章 ID，
  配合回执重放不会重复建章；不同预览得到不同身份（同内容再次导入是副本）。
- **去重看内容不看文件名**：指纹按段落序列计算，空行数量不同不影响判等。
- 重复内容的三种处理：取消、新增副本、替换。选择「替换」时必须逐章给出
  目标与当前章基础；同名不判等、不覆盖（计划 M06 第 2/3 条）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from hastings.application.capabilities import IMPORT_LIMITS
from hastings.application.errors import ViolationError
from hastings.application.handlers.manuscript_commands import manuscript_command
from hastings.application.ports.ids import IdGenerator
from hastings.application.services.commit_service import CommitService
from hastings.application.services.edit_session_service import EditSessionService
from hastings.application.services.publish_manuscript_change import PublishManuscriptChange
from hastings.domain.manuscript import commands as manuscript_commands
from hastings.domain.manuscript import error_codes as ec
from hastings.domain.manuscript.catalog import ManuscriptCatalog, next_order_keys
from hastings.domain.manuscript.kinds import KIND_CHAPTER
from hastings.domain.repository.ids import Actor, ProjectId
from hastings.domain.repository.requests import AuxWrite, CommitRequest
from hastings.domain.repository.serialization import content_hash
from hastings.interfaces.importers.manuscript_text import (
    SUPPORTED_SUFFIXES,
    ParsedManuscript,
    decode_manuscript_bytes,
    parse_manuscript_text,
)

__all__ = ["ImportService", "PREVIEW_BUCKET", "PREVIEW_TTL"]

#: 预览在辅助表中的 bucket（key = preview_id）。
PREVIEW_BUCKET = "import_preview"

#: 预览时效（计划 M06：持久预览 24 小时）。
PREVIEW_TTL = timedelta(hours=24)

_AUTHOR = Actor(kind="human", id="author")

_DECISIONS = ("cancel", "append", "replace")


class ImportService:
    """导入预览与确认；确认复用统一发布管线（不另建写路径）。"""

    def __init__(
        self,
        repo: CommitService,
        publisher: PublishManuscriptChange,
        ids: IdGenerator,
        clock: Any,
        edit_sessions: EditSessionService | None = None,
    ) -> None:
        self._repo = repo
        self._publisher = publisher
        self._ids = ids
        self._clock: Any = clock
        self._edit_sessions = edit_sessions

    # -- 预览 -----------------------------------------------------------------

    def create_preview(self, project_id: str, filename: str, data: bytes) -> dict[str, Any]:
        """解析并持久化预览；不写任何正式正文。"""
        suffix = _suffix(filename)
        if suffix not in SUPPORTED_SUFFIXES:
            raise _violation(
                ec.IMPORT_UNSUPPORTED_FORMAT,
                f"imports.preview({filename})",
                f"文件格式 {suffix or '（无扩展名）'} 不受支持",
                f"本版只接受 {' / '.join(SUPPORTED_SUFFIXES)} 的 UTF-8 文本",
            )
        if len(data) > IMPORT_LIMITS["max_file_bytes"]:
            raise _violation(
                ec.IMPORT_LIMIT_EXCEEDED,
                f"imports.preview({filename})",
                f"文件 {len(data)} 字节超过单文件上限 {IMPORT_LIMITS['max_file_bytes']}",
                "拆分文件后分批导入",
            )
        parsed = parse_manuscript_text(decode_manuscript_bytes(data), filename=filename)
        self._check_limits(parsed, filename)
        catalog = self._catalog(ProjectId(project_id))
        duplicates = self._find_duplicates(catalog, parsed)
        order_keys = self._append_order_keys(catalog, len(parsed.chapters))
        now = self._clock.now()
        preview_id = self._ids.new_id("imp")
        payload: dict[str, Any] = {
            "preview_id": preview_id,
            "project_id": project_id,
            "filename": filename,
            "format": parsed.format,
            "created_at": now.isoformat(),
            "expires_at": (now + PREVIEW_TTL).isoformat(),
            "revision": 1,
            "batch_hash": parsed.batch_hash,
            "chapters": [
                {
                    "index": index,
                    "title": chapter.title,
                    "text": chapter.text,
                    "order_key": order_keys[index],
                    "content_hash": chapter.content_hash,
                }
                for index, chapter in enumerate(parsed.chapters)
            ],
            "duplicates": duplicates,
            "selection": {"mode": "append", "replacements": []},
        }
        payload["preview_hash"] = _preview_hash(payload)
        self._write_preview(ProjectId(project_id), payload)
        return _public(payload)

    def get_preview(self, project_id: str, preview_id: str) -> dict[str, Any]:
        return _public(self._load(ProjectId(project_id), preview_id, check_expiry=False))

    def update_selection(
        self,
        project_id: str,
        preview_id: str,
        *,
        expected_revision: int,
        mode: str,
        replacements: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """调整目标：新增或显式替换；替换必须给目标与当前章基础。"""
        pid = ProjectId(project_id)
        payload = self._load(pid, preview_id)
        if payload["revision"] != expected_revision:
            raise _violation(
                ec.IMPORT_PREVIEW_CONFLICT,
                f"imports.update({preview_id})",
                f"预览已被修改（当前 revision {payload['revision']}，请求 {expected_revision}）",
                "重新读取预览后再提交目标变更",
            )
        if mode not in ("append", "replace"):
            raise _violation(
                ec.IMPORT_PREVIEW_CONFLICT,
                f"imports.update({preview_id})",
                f"未知的目标模式 {mode!r}",
                "使用 append（新增）或 replace（显式替换）",
            )
        if mode == "replace" and not replacements:
            raise _violation(
                ec.IMPORT_REPLACEMENT_REQUIRED,
                f"imports.update({preview_id})",
                "替换模式没有给出目标章",
                "为每个被替换的章指定 chapter_id 与当前 chapter_base",
            )
        catalog = self._catalog(pid)
        normalized = self._validate_replacements(catalog, payload, replacements)
        payload = dict(payload)
        payload["revision"] = int(payload["revision"]) + 1
        payload["selection"] = {"mode": mode, "replacements": normalized}
        payload["preview_hash"] = _preview_hash(payload)
        self._write_preview(pid, payload)
        return _public(payload)

    # -- 确认 -----------------------------------------------------------------

    def confirm(
        self,
        project_id: str,
        preview_id: str,
        *,
        preview_hash: str,
        duplicate_decision: str | None = None,
        operation_id: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """整批确认：一次发布，失败无半份正文；重试不重复建章。"""
        pid = ProjectId(project_id)
        payload = self._load(pid, preview_id)
        if payload["preview_hash"] != preview_hash:
            raise _violation(
                ec.IMPORT_PREVIEW_CONFLICT,
                f"imports.confirm({preview_id})",
                "预览内容或目标已变化，与作者看到的不是同一份",
                "重新读取预览并核对文本与目标后再确认",
            )
        duplicates: list[dict[str, Any]] = list(payload.get("duplicates") or [])
        if duplicate_decision is not None and duplicate_decision not in _DECISIONS:
            raise _violation(
                ec.IMPORT_DUPLICATE_DECISION_REQUIRED,
                f"imports.confirm({preview_id})",
                f"未知的重复处理决定 {duplicate_decision!r}",
                "选择 cancel（取消）、append（新增副本）或 replace（替换指定章）",
            )
        if duplicates and duplicate_decision is None:
            raise _violation(
                ec.IMPORT_DUPLICATE_DECISION_REQUIRED,
                f"imports.confirm({preview_id})",
                f"本批有 {len(duplicates)} 章与现有正文内容相同，不能默认处理",
                "明确选择取消、新增副本或替换指定章；同名不等于同内容，也不自动覆盖",
            )
        if duplicate_decision == "cancel":
            return {
                "status": "cancelled",
                "preview_id": preview_id,
                "project_id": project_id,
                "chapter_ids": [],
            }

        selection = payload.get("selection") or {"mode": "append", "replacements": []}
        if duplicate_decision == "append" and selection.get("mode") != "append":
            raise _violation(
                ec.IMPORT_PREVIEW_CONFLICT,
                f"imports.confirm({preview_id})",
                "预览已选择替换目标，但确认时选择新增副本",
                "统一为一种处理：要么改预览目标，要么改为替换",
            )

        catalog = self._catalog(pid)
        replacements, covered = self._plan_replacements(
            catalog, payload, selection, duplicates, duplicate_decision
        )
        self._reject_edit_session_overlap(pid, {item.chapter_id for item in replacements})
        additions = [
            manuscript_commands.ChapterImport(
                chapter_id=_chapter_id(preview_id, int(chapter["index"])),
                title=str(chapter["title"]),
                text=str(chapter["text"]),
                order_key=str(chapter["order_key"]),
            )
            for chapter in payload["chapters"]
            if int(chapter["index"]) not in covered
        ]
        if not additions and not replacements:
            return {
                "status": "no_change",
                "preview_id": preview_id,
                "project_id": project_id,
                "chapter_ids": [],
            }

        op_id = operation_id or (
            "op_"
            + content_hash(
                ["import.confirm", project_id, preview_id, preview_hash, duplicate_decision]
            )[:16]
        )
        command = manuscript_command(
            manuscript_commands.import_batch,
            additions=tuple(additions),
            replacements=tuple(replacements),
        )
        result = self._publisher.publish_command(
            project_id=pid,
            command=command,
            operation_id=op_id,
            kind="chapter.import",
            summary=(
                f"导入「{payload['filename']}」：新增 {len(additions)} 章、"
                f"替换 {len(replacements)} 章"
            ),
            refs={"preview_id": preview_id, "batch_hash": payload["batch_hash"]},
            origin_conversation_id=conversation_id,
        )
        return {
            "status": result.status,
            "preview_id": preview_id,
            "project_id": project_id,
            "operation_id": result.operation_id,
            "commit_id": result.commit_id,
            "changed_chapter_ids": list(result.envelope.changed_chapter_ids),
            "chapter_ids": [item.chapter_id for item in additions]
            + [item.chapter_id for item in replacements],
        }

    # -- 内部 -----------------------------------------------------------------

    def _plan_replacements(
        self,
        catalog: ManuscriptCatalog,
        payload: dict[str, Any],
        selection: dict[str, Any],
        duplicates: list[dict[str, Any]],
        decision: str | None,
    ) -> tuple[list[manuscript_commands.ChapterReplacement], set[int]]:
        """确定替换目标与被覆盖的预览章序号。

        替换模式下每个内容重复的章都必须有显式目标（计划 M06：替换逐章明确
        目标与基础版本）；与预览内容不重复的章也可以显式替换，但需要指明用
        哪一章预览正文（``index``）。
        """
        explicit = {str(item["chapter_id"]): item for item in selection.get("replacements", [])}
        replacements: list[manuscript_commands.ChapterReplacement] = []
        covered: set[int] = set()
        if decision == "replace":
            for duplicate in duplicates:
                index = int(duplicate["index"])
                matched = str(duplicate["chapter_id"])
                override = explicit.pop(matched, None)
                if override is None:
                    raise _violation(
                        ec.IMPORT_REPLACEMENT_REQUIRED,
                        f"imports.confirm({matched})",
                        "选择替换，但没有为该重复章指定替换目标",
                        "替换必须逐章明确目标与基础版本；或改为新增副本",
                    )
                replacements.append(
                    manuscript_commands.ChapterReplacement(
                        chapter_id=matched,
                        text=str(payload["chapters"][index]["text"]),
                        expected_chapter_base=override.get("expected_chapter_base")
                        or catalog.chapter_base(matched),
                    )
                )
                covered.add(index)
        # 剩余显式目标：替换与预览内容不重复的章（作者明确指定）。
        for item in explicit.values():
            index = item.get("index")
            if index is None:
                raise _violation(
                    ec.IMPORT_REPLACEMENT_REQUIRED,
                    f"imports.confirm({item['chapter_id']})",
                    "替换目标与预览内容不重复，却没有指明用哪一章预览正文",
                    "在目标里给出 index（预览中的章序号）",
                )
            replacements.append(
                manuscript_commands.ChapterReplacement(
                    chapter_id=str(item["chapter_id"]),
                    text=str(payload["chapters"][int(index)]["text"]),
                    expected_chapter_base=item.get("expected_chapter_base")
                    or catalog.chapter_base(str(item["chapter_id"])),
                )
            )
            covered.add(int(index))
        return replacements, covered

    def _reject_edit_session_overlap(self, pid: ProjectId, targets: set[str]) -> None:
        """与未结束修改副本重叠：要求先完成或放弃本轮，不自动放弃副本。

        M06 第 3 条。只拦**替换**目标——新增章不触碰副本里的章；副本自身的
        基础版本校验会独立拦住「副本期间正式稿被导入替换」的情况。
        """
        if not targets or self._edit_sessions is None:
            return
        current = self._edit_sessions.current(pid.value)
        if current is None:
            return
        drafts = {str(draft["chapter_id"]) for draft in current.get("drafts", [])}
        blocked = sorted(drafts & targets)
        if not blocked:
            return
        raise _violation(
            ec.EDIT_SESSION_CONFLICT,
            f"imports.confirm({pid.value})",
            f"章节 {blocked} 正在手工修改副本中，导入不能替换",
            "先完成或放弃这一轮修改，再重新导入；系统不会自动放弃副本",
            chapter_ids=blocked,
        )

    def _check_limits(self, parsed: ParsedManuscript, filename: str) -> None:
        if not parsed.chapters or all(not chapter.text for chapter in parsed.chapters):
            raise _violation(
                ec.IMPORT_EMPTY_CONTENT,
                f"imports.preview({filename})",
                "文件里没有可导入的正文",
                "确认文件不是空白或只有标题行",
            )
        if len(parsed.chapters) > IMPORT_LIMITS["max_chapters_per_batch"]:
            raise _violation(
                ec.IMPORT_LIMIT_EXCEEDED,
                f"imports.preview({filename})",
                f"解析出 {len(parsed.chapters)} 章，超过单批上限 "
                f"{IMPORT_LIMITS['max_chapters_per_batch']}",
                "按卷或按部分拆成多批导入",
            )

    def _catalog(self, pid: ProjectId) -> ManuscriptCatalog:
        branch = self._repo.get_branch(pid, "main")
        if branch is None:
            raise _violation(
                ec.IMPORT_UNKNOWN_TARGET,
                f"imports.catalog({pid.value})",
                f"作品 {pid.value} 没有 main 分支",
                "先创建作品再导入正文",
            )
        head = branch.head_commit_id
        if head is None:
            return ManuscriptCatalog(commit_id=None, entries=())
        return ManuscriptCatalog.from_snapshot(self._repo.get_snapshot(pid, head))

    def _find_duplicates(
        self, catalog: ManuscriptCatalog, parsed: ParsedManuscript
    ) -> list[dict[str, Any]]:
        """内容相同的现有章：只做提示，不替作者决定覆盖或跳过。"""
        existing: dict[str, str] = {}
        titles: dict[str, str] = {}
        for entry in catalog.entries:
            if entry.kind != KIND_CHAPTER:
                continue
            chapter = entry.model()
            text_hash = content_hash(
                {"paragraphs": [unit.model().text for unit in catalog.units_of(entry.artifact_id)]}
            )
            existing.setdefault(text_hash, entry.artifact_id)
            titles[entry.artifact_id] = chapter.title
        duplicates: list[dict[str, Any]] = []
        for index, chapter in enumerate(parsed.chapters):
            chapter_id = existing.get(chapter.content_hash)
            if chapter_id is None:
                continue
            duplicates.append(
                {
                    "index": index,
                    "title": chapter.title,
                    "chapter_id": chapter_id,
                    "existing_title": titles.get(chapter_id, ""),
                    "existing_chapter_base": catalog.chapter_base(chapter_id),
                    "content_hash": chapter.content_hash,
                }
            )
        return duplicates

    @staticmethod
    def _append_order_keys(catalog: ManuscriptCatalog, count: int) -> list[str]:
        return next_order_keys(catalog, count)

    def _validate_replacements(
        self,
        catalog: ManuscriptCatalog,
        payload: dict[str, Any],
        replacements: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        seen: set[str] = set()
        normalized: list[dict[str, Any]] = []
        for item in replacements:
            chapter_id = str(item.get("chapter_id", "")).strip()
            if not chapter_id:
                raise _violation(
                    ec.IMPORT_UNKNOWN_TARGET,
                    "imports.update",
                    "替换目标缺少 chapter_id",
                    "为每个替换目标指定章 ID",
                )
            if chapter_id in seen:
                raise _violation(
                    ec.IMPORT_DUPLICATE_TARGET,
                    "imports.update",
                    f"同一批导入重复指向章节 {chapter_id}",
                    "每个替换目标只出现一次",
                )
            seen.add(chapter_id)
            if catalog.get(chapter_id) is None:
                raise _violation(
                    ec.IMPORT_UNKNOWN_TARGET,
                    "imports.update",
                    f"作品里没有章节 {chapter_id}",
                    "刷新目录后重新选择替换目标",
                )
            current = catalog.chapter_base(chapter_id)
            expected = item.get("expected_chapter_base")
            if expected is not None and str(expected) != current:
                raise _violation(
                    ec.MANUSCRIPT_BASE_CONFLICT,
                    f"imports.update({chapter_id})",
                    "目标章在预览之后已被修改",
                    "重新读取该章并重新生成预览后再替换",
                    chapter_id=chapter_id,
                    expected=str(expected),
                    current=current,
                )
            index = item.get("index")
            if index is not None:
                index = int(index)
                if index not in {int(c["index"]) for c in payload["chapters"]}:
                    raise _violation(
                        ec.IMPORT_UNKNOWN_TARGET,
                        "imports.update",
                        f"预览中没有第 {index} 章",
                        "使用预览里存在的章序号",
                    )
            elif chapter_id not in {
                str(item["chapter_id"]) for item in payload.get("duplicates") or []
            }:
                raise _violation(
                    ec.IMPORT_REPLACEMENT_REQUIRED,
                    f"imports.update({chapter_id})",
                    "该章与预览内容不重复，需要指明用哪一章预览正文替换",
                    "在目标里给出 index（预览中的章序号）",
                )
            entry: dict[str, Any] = {
                "chapter_id": chapter_id,
                "expected_chapter_base": current,
            }
            if index is not None:
                entry["index"] = index
            normalized.append(entry)
        return normalized

    def _load(
        self, pid: ProjectId, preview_id: str, *, check_expiry: bool = True
    ) -> dict[str, Any]:
        stored = self._repo.aux_read(pid, PREVIEW_BUCKET, preview_id)
        if stored is None:
            raise _violation(
                ec.IMPORT_PREVIEW_NOT_FOUND,
                f"imports.preview({preview_id})",
                "预览不存在或已被清理",
                "重新上传文件生成预览",
            )
        payload = dict(stored)
        if check_expiry and _expired(payload, self._clock.now()):
            raise _violation(
                ec.IMPORT_PREVIEW_EXPIRED,
                f"imports.preview({preview_id})",
                "预览已超过 24 小时",
                "重新上传文件生成预览；已导入的正文不受影响",
            )
        return payload

    def _write_preview(self, pid: ProjectId, payload: dict[str, Any]) -> None:
        """预览落辅助表：只有 aux_writes 的提交，不产生正文版本。"""
        branch = self._repo.get_branch(pid, "main")
        if branch is None:
            raise _violation(
                ec.IMPORT_UNKNOWN_TARGET,
                f"imports.preview({pid.value})",
                f"作品 {pid.value} 没有 main 分支",
                "先创建作品再导入正文",
            )
        self._repo.commit(
            CommitRequest(
                project_id=pid,
                branch_id=branch.id,
                expected_head=branch.head_commit_id,
                actor=_AUTHOR,
                idempotency_key=f"import_preview:{payload['preview_id']}:{payload['revision']}",
                summary=f"导入预览「{payload['filename']}」r{payload['revision']}",
                aux_writes=(AuxWrite(PREVIEW_BUCKET, str(payload["preview_id"]), dict(payload)),),
            )
        )


# -- 纯函数 -----------------------------------------------------------------


def _preview_hash(payload: dict[str, Any]) -> str:
    """预览哈希：正文指纹 + 目标选择（作者看到的文本与目标）。"""
    return content_hash(
        {
            "chapters": [chapter["content_hash"] for chapter in payload["chapters"]],
            "selection": payload.get("selection") or {},
            "revision": payload.get("revision", 1),
        }
    )


def _chapter_id(preview_id: str, index: int) -> str:
    """新增章身份：同一预览的重试落回同一 ID，不同预览得到不同章。"""
    return "art_" + content_hash(["import", preview_id, str(index)])[:16]


def _expired(payload: dict[str, Any], now: datetime) -> bool:
    expires = payload.get("expires_at")
    if not isinstance(expires, str):
        return True
    try:
        return datetime.fromisoformat(expires) <= now
    except ValueError:
        return True


def _public(payload: dict[str, Any]) -> dict[str, Any]:
    """对外视图：去掉内部字段，保留作者需要核对的全部内容。"""
    return {key: value for key, value in payload.items() if key != "project_id"}


def _suffix(filename: str) -> str:
    name = filename.strip().lower()
    dot = name.rfind(".")
    return name[dot:] if dot >= 0 else ""


def _violation(code: str, where: str, why: str, fix: str, **details: Any) -> ViolationError:
    return ViolationError(code=code, where=where, why=why, fix=fix, details=details)

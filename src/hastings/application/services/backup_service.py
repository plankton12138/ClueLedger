"""备份预览与同作品恢复（V03-8.2）：把既有导出能力接成产品面，并说清覆盖范围。

职责边界：

- **备份预览**只读：按当前 head 构建导出包，回报清单、计数与整包指纹，不写数据；
- **恢复**只经 ``PublishManuscriptChange``：包内正文与账本走同一条统一发布管线，
  一个 Commit 生效，操作 ID 绑定整包指纹与确认状态（确认前后是不同意图）。

为什么恢复是「同作品」而不是「搬到新作品」：artifact ID 归属创建它的项目（仓库
不变体），同库内跨项目搬移 ID 会被拒绝；把备份搬进新作品需要 ID 重映射表，0.3 不
做。备份的用途因此是明确的——**把这个作品恢复到备份时的版本**（误删、误改、试错
之后），原历史保留为新提交，不做「移动」。

覆盖范围显式声明（计划 §6.5 补充合同）：包装的是**工作内容**——正文、账本（含约定
与结构）与依赖边；过程状态与凭据不在包里：副本与暂存是工作状态、操作/建议/日志是
过程记录（结果已体现在正文与账本）、凭据与模型连接在服务器配置与 OS 凭据存储里。
把「不在包里」说清楚，比含糊地说「完整备份」更可信。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import yaml

from hastings.application.errors import ViolationError
from hastings.application.handlers.manuscript_commands import (
    ManuscriptCommand,
    manuscript_command,
)
from hastings.application.ports.repository import CommitRepository, RepositoryReader
from hastings.application.services.publish_manuscript_change import PublishManuscriptChange
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.manuscript.commands import save_full_text
from hastings.domain.repository.ids import ArtifactId, ProjectId, RevisionId
from hastings.domain.repository.requests import ArtifactChange
from hastings.domain.repository.serialization import content_hash
from hastings.domain.story.catalog import StoryCatalog
from hastings.interfaces.exporters.v2_package_exporter import (
    EXPORT_KIND,
    export_commit,
    unzip_package,
    verify_package,
)

__all__ = ["BACKUP_COVERAGE", "BackupService", "RESTORE_OPERATION_PREFIX"]

#: 恢复操作的 ID 前缀：整包指纹 + 确认状态决定同一次恢复；确认前后意图不同。
RESTORE_OPERATION_PREFIX = "op_restore"

#: 覆盖范围声明：进包的与不进包的，各带归属说明。前端与文档共用同一份口径。
BACKUP_COVERAGE: dict[str, Any] = {
    "included": [
        {"what": "正文章节与段落清单", "where": "chapters/<order_key>.md + manifest 段落清单"},
        {"what": "故事账本条目（人物/地点/线索/真相/认知/披露/时间线等）",
         "where": "bible/<目录>/<artifact_id>.yaml"},
        {
            "what": "本书约定与叙事结构",
            "where": "bible/agreements/、bible/structure/、bible/outline/",
        },
        {"what": "条目间依赖边", "where": "relations.json"},
    ],
    "excluded": [
        {"what": "手工修改副本与修改稿快照", "where": "aux 表 edit_session",
         "why": "工作状态，不属于工作内容；恢复后按需重新开始副本"},
        {"what": "覆盖台账/整体任务/暂存与关联链接",
         "where": "aux 表 revision_work / revision_task / revision_stage / revision_links",
         "why": "执行过程状态，重新执行即可重建"},
        {"what": "操作记录、待确认建议与候选组", "where": "operation 记录 / candidates",
         "why": "过程记录；其结果已体现在正文与账本里"},
        {"what": "执行日志", "where": "execution_log 事件流",
         "why": "展示投影，可由仓库与任务记录重放"},
        {"what": "模型连接与凭据", "where": "服务器配置 + OS 凭据存储",
         "why": "与项目数据分离，凭据绝不进入备份包"},
    ],
}


class BackupService:
    """只读预览 + 经统一发布路径的同作品恢复。"""

    def __init__(
        self,
        service: CommitRepository,
        reader: RepositoryReader,
        publisher: PublishManuscriptChange,
    ) -> None:
        self._repo = service
        self._reader = reader
        self._publisher = publisher

    # -- 预览 ----------------------------------------------------------------
    def preview(self, project_id: str) -> dict[str, Any]:
        """按当前 head 构建备份包的清单与指纹；不写任何数据。"""
        pid = ProjectId(project_id)
        branch = self._reader.get_branch(pid, "main")
        head = branch.head_commit_id if branch is not None else None
        if head is None:
            return {
                "project_id": project_id,
                "kind": EXPORT_KIND,
                "commit_id": None,
                "package_hash": None,
                "counts": {"chapters": 0, "story_entities": 0, "relations": 0, "chars": 0},
                "files": [],
                "coverage": BACKUP_COVERAGE,
                "note": "项目还没有任何提交，没有可备份的内容。",
            }
        package = export_commit(self._reader, pid, head)
        return {
            "project_id": project_id,
            "kind": EXPORT_KIND,
            "commit_id": package.commit_id,
            "package_hash": package.package_hash,
            "counts": dict(package.manifest["counts"]),
            "files": [
                {"path": entry["path"], "kind": entry["kind"], "sha256": entry["sha256"]}
                for entry in package.manifest["files"]
            ],
            "coverage": BACKUP_COVERAGE,
            "note": "预览与导出用同一份确定性字节；下载请用 GET /projects/{id}/export。",
        }

    # -- 恢复 ----------------------------------------------------------------
    def restore(
        self,
        project_id: str,
        archive: bytes,
        *,
        confirm_overwrite: bool = False,
    ) -> dict[str, Any]:
        """把这个作品恢复到备份版本：一次原子提交，原历史保留。

        与撤销同构但不相同：撤销按**操作记录**恢复操作前状态；这里按**包内容**
        恢复。所以必须先把差异摊开给作者看——没有任何确认就盖掉当前内容，是二次
        伤害而不是恢复。

        - 包内容与当前完全一致 ⇒ 合法空结果，不产生新版本；
        - 有差异且未确认 ⇒ 拒绝并列出会被改写的章与账本字段，等作者确认；
        - 确认后 ⇒ 正文命令 + 账本变化经 ``publish_commands`` 进同一个 Commit。
        """
        # 写入 Actor 由统一发布管线按作者身份固定；恢复不换身份、不换归属。
        pid = ProjectId(project_id)
        files = unzip_package(archive)
        manifest = verify_package(files)
        package_hash = str(manifest.get("package_hash", ""))
        operation_id = (
            f"{RESTORE_OPERATION_PREFIX}_{content_hash([package_hash, confirm_overwrite])[:16]}"
        )

        diff = self._diff_against_current(pid, files)
        if not diff["chapters"] and not diff["ledger"]:
            return {
                "status": "no_change",
                "operation_id": operation_id,
                "commit_id": None,
                "new_head": None,
                "package_hash": package_hash,
                "source_commit_id": manifest.get("commit_id"),
                "message": "当前内容与备份一致，没有需要恢复的变化。",
            }
        if not confirm_overwrite:
            raise ViolationError(
                code="EXPORT_RESTORE_CONFLICT",
                where=f"v2.restore({project_id})",
                why=(
                    f"备份与当前内容有 {len(diff['chapters'])} 章、"
                    f"{len(diff['ledger'])} 条账本差异；未确认不会改写任何内容"
                ),
                fix="确认按备份覆盖这些内容（confirm_overwrite=true），或取消恢复",
                details={
                    "chapters": diff["chapters"],
                    "ledger": diff["ledger"],
                    "choices": ["confirm_overwrite", "cancel"],
                },
            )

        result = self._publisher.publish_commands(
            project_id=pid,
            commands=tuple(diff["commands"]),
            operation_id=operation_id,
            kind="backup.restore",
            summary=f"从备份恢复（{len(diff['commands'])} 章 / {len(diff['ledger'])} 条账本）",
            refs={"package_hash": package_hash, "source_commit_id": manifest.get("commit_id")},
            extra_changes=tuple(diff["ledger_changes"]),
        )
        return {
            "status": result.status,
            "operation_id": result.operation_id,
            "commit_id": result.commit_id,
            "new_head": result.new_head,
            "package_hash": package_hash,
            "source_commit_id": manifest.get("commit_id"),
            "restored_chapters": [row["chapter_id"] for row in diff["chapters"]],
            "restored_ledger_ids": [row["artifact_id"] for row in diff["ledger"]],
            "warnings": list(result.warnings),
        }

    # -- 只读差异 ------------------------------------------------------------
    def _diff_against_current(self, pid: ProjectId, files: dict[str, bytes]) -> dict[str, Any]:
        """把包内容与当前 head 对照：返回差异、正文命令与账本变化。

        差异是**实际内容比较**：包的章文本/账本内容与当前快照逐字比较，不按
        时间戳、不按 revision 猜。相同就不动，不同才进入恢复集合；包里有、当前
        没有的对象显式拒绝——恢复不新建、不 ID 重映射。
        """
        manifest = verify_package(files)
        branch = self._reader.get_branch(pid, "main")
        head = branch.head_commit_id if branch is not None else None
        if head is None:
            raise ViolationError(
                code="REPO_BRANCH_NOT_FOUND",
                where="v2.restore.diff",
                why="作品还没有任何提交，没有可恢复的目标",
                fix="先导入正文或创建章节，再用备份恢复",
                details={"project_id": pid.value},
            )
        snapshot = self._reader.get_snapshot(pid, head)
        ms_catalog = ManuscriptCatalog.from_snapshot(snapshot)
        story_catalog = StoryCatalog.from_snapshot(snapshot)

        chapters: list[dict[str, Any]] = []
        commands: list[ManuscriptCommand] = []
        ledger: list[dict[str, Any]] = []
        ledger_changes: list[ArtifactChange] = []

        for entry in manifest.get("files", []):
            artifact_id = str(entry.get("artifact_id") or "")
            kind = str(entry.get("kind") or "")
            if kind == "manuscript.chapter":
                text = files[entry["path"]].decode("utf-8")
                if ms_catalog.chapter_entry(artifact_id) is None:
                    raise ViolationError(
                        code="EXPORT_RESTORE_UNKNOWN_CHAPTER",
                        where=f"v2.restore({artifact_id})",
                        why=f"备份里的章 {artifact_id!r} 在当前作品里不存在",
                        fix="恢复只针对同一作品的备份；已不在的作品请改用包导入",
                        details={"chapter_id": artifact_id},
                    )
                current_text = "\n\n".join(
                    unit.model().text for unit in ms_catalog.units_of(artifact_id)
                )
                if current_text == text:
                    continue
                current_base = ms_catalog.chapter_base(artifact_id)
                chapters.append(
                    {
                        "chapter_id": artifact_id,
                        "title": entry.get("title", ""),
                        "path": entry["path"],
                        "current_base": current_base,
                    }
                )
                commands.append(
                    manuscript_command(
                        save_full_text,
                        chapter_id=artifact_id,
                        text=text,
                        expected_chapter_base=current_base,
                    )
                )
            elif artifact_id:
                payload: Mapping[str, Any] = yaml.safe_load(files[entry["path"]])
                content = dict(payload["content"])
                existing = story_catalog.get(artifact_id)
                if existing is None:
                    raise ViolationError(
                        code="EXPORT_RESTORE_NEW_OBJECT",
                        where=f"v2.restore({artifact_id})",
                        why=f"备份里的账本条目 {artifact_id!r} 在当前作品里不存在",
                        fix="恢复不新建条目；请先在账本创建该条目，或把包导入空作品",
                        details={"artifact_id": artifact_id},
                    )
                if dict(existing.content) == content:
                    continue
                ledger.append(
                    {
                        "artifact_id": artifact_id,
                        "kind": kind,
                        "path": entry["path"],
                        "current_revision": existing.revision_id,
                        "changed_fields": sorted(
                            key
                            for key in set(existing.content) | set(content)
                            if existing.content.get(key) != content.get(key)
                        ),
                    }
                )
                ledger_changes.append(
                    ArtifactChange(
                        artifact_id=ArtifactId(artifact_id),
                        content=content,
                        old_revision_id=RevisionId(existing.revision_id),
                        kind=kind,
                    )
                )
        return {
            "chapters": chapters,
            "commands": commands,
            "ledger": ledger,
            "ledger_changes": ledger_changes,
        }

"""Phase 17 导出：指定 Commit → 可读备份包（Markdown/YAML/JSON + manifest 哈希）。

职责边界：本模块是**只读**出口——从 Repository 快照产出可审阅、可核对的包；
写回只经 `apply_exported_package` 走 Commit Service，不绕过幂等表直连存储。

包布局（同一次导出内全部路径固定、字节确定，无时间戳/顺序漂移）：

- ``manifest.json`` —— schema 版本、项目与 Commit 标识、逐文件 sha256 与
  段落清单（unit_id + 文本哈希）、汇总计数、整包指纹 ``package_hash``；
- ``chapters/<order_key>.md`` —— 章正文（只存活动段落文本，标题与结构在
  manifest 里，Markdown 保持纯净可读）；
- ``bible/<账本>/<artifact_id>.yaml`` —— 每个故事对象一个文件：
  ``{artifact_id, kind, content}``，ID 稳定可直接重导入；
- ``relations.json`` —— 快照依赖边（排序后序列化），供重导入逐条还原。

与导入器的同构：``package_hash`` 采用与迁移 ``manifest_hash`` 相同的
``路径\\0哈希\\0`` 指纹形状；重导入幂等键 = ``v2-export:{schema}:{package_hash}``。
往返（导出 → 重导入）保持 artifact id、章节基础（unit_id+文本）与依赖边逐位一致，
这是「导出可用」的可核对定义，也是容量演练与验收报告的度量对象。

损坏防护（泄压阀）：``verify_package`` 校验每个文件的 sha256 与整包指纹，
任何不一致都带三要素显式拒绝——备份的价值在于可信，静默接受损坏包比没有备份更糟。
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from hastings.application.errors import ViolationError
from hastings.application.ports.repository import CommitRepository, RepositoryReader
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.manuscript.commands import import_chapter
from hastings.domain.repository import error_codes as ec
from hastings.domain.repository.ids import Actor, ArtifactId, CommitId, ProjectId
from hastings.domain.repository.requests import ArtifactChange, CommitRequest, RelationChange
from hastings.domain.repository.results import CommitResult
from hastings.domain.story import kinds as story_kinds
from hastings.manuscript.diff import split_paragraphs

__all__ = [
    "EXPORT_KIND",
    "EXPORT_SCHEMA_VERSION",
    "ExportImportResult",
    "ExportPackage",
    "apply_exported_package",
    "export_commit",
    "package_hash_of",
    "read_package",
    "unzip_package",
    "verify_package",
    "write_package",
    "zip_package",
]

# 导出包 schema 版本：布局语义变化必须递增，旧包重导入按旧语义处理
EXPORT_SCHEMA_VERSION = 1
EXPORT_KIND = "hastings.v2.export"

_EVENT_PACKAGE_IMPORTED = "v2.package.imported"

# 故事对象 kind → 包内账本目录（可读布局；新增 kind 必须显式登记，不静默归类）
_BIBLE_DIRS: dict[str, str] = {
    story_kinds.KIND_STORY_PREMISE: "premise",
    story_kinds.KIND_CHARACTER: "characters",
    story_kinds.KIND_RELATIONSHIP: "relationships",
    story_kinds.KIND_PLACE: "places",
    story_kinds.KIND_WORLD_RULE: "world_rules",
    story_kinds.KIND_MYSTERY_TRUTH: "truth",
    story_kinds.KIND_CLUE: "clues",
    story_kinds.KIND_RED_HERRING: "red_herrings",
    story_kinds.KIND_FORESHADOW: "foreshadows",
    story_kinds.KIND_TIMELINE_EVENT: "timeline",
    story_kinds.KIND_TRUTH_FACT: "truth_facts",
    story_kinds.KIND_KNOWLEDGE: "knowledge",
    story_kinds.KIND_DISCLOSURE: "disclosures",
    # 本书约定（V03-4.1）：作品级资料，不属于可引用账本，单独一个目录便于人工核对。
    story_kinds.KIND_AGREEMENT: "agreements",
    story_kinds.KIND_SERIES: "structure",
    story_kinds.KIND_BOOK: "structure",
    story_kinds.KIND_ARC: "structure",
    story_kinds.KIND_CHAPTER: "outline",
    story_kinds.KIND_SCENE: "structure",
    story_kinds.KIND_BEAT: "beats",
}

_ENTRY_CHAPTER = "manuscript.chapter"
_ENTRY_RELATIONS = "meta.relations"


@dataclass(frozen=True, slots=True)
class ExportPackage:
    """一次导出的完整结果：文件字典（含 manifest.json）+ manifest + 整包指纹。"""

    project_id: str
    project_name: str
    commit_id: str
    files: dict[str, bytes]
    manifest: dict[str, Any]
    package_hash: str


@dataclass(frozen=True, slots=True)
class ExportImportResult:
    """一次重导入的结论；``replayed`` 表示命中幂等记录。"""

    status: str
    commit_id: str | None
    new_head: str | None
    replayed: bool


# --------------------------------------------------------------------------
# 指纹
# --------------------------------------------------------------------------
def package_hash_of(manifest: dict[str, Any]) -> str:
    """整包指纹：manifest.files 里「路径 + 逐文件 sha256」的稳定哈希。

    与迁移导入器的 manifest_hash 同形（``路径\\0哈希\\0`` 串接后取 sha256），
    同一内容在任何机器上得到同一指纹——幂等与核对都靠它，不靠 mtime/时间戳。
    """
    digest = hashlib.sha256()
    for entry in sorted(manifest["files"], key=lambda e: e["path"]):
        digest.update(f"{entry['path']}\x00{entry['sha256']}\x00".encode())
    return digest.hexdigest()


def _file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(data: Any) -> bytes:
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _yaml_bytes(data: dict[str, Any]) -> bytes:
    return yaml.safe_dump(
        data, allow_unicode=True, sort_keys=True, default_flow_style=False
    ).encode("utf-8")


# --------------------------------------------------------------------------
# 导出（只读）
# --------------------------------------------------------------------------
def export_commit(
    reader: RepositoryReader, project_id: ProjectId, commit_id: CommitId
) -> ExportPackage:
    """从指定 Commit 的快照构建导出包；不写 Repository、不产生时间戳。"""
    snapshot = reader.get_snapshot(project_id, commit_id)
    project = reader.get_project(project_id)
    project_name = project.name if project is not None else project_id.value

    ms_catalog = ManuscriptCatalog.from_snapshot(snapshot)
    entries: list[dict[str, Any]] = []
    files: dict[str, bytes] = {}
    chars = 0

    for entry in ms_catalog.chapters():
        chapter = entry.model()
        units = ms_catalog.units_of(entry.artifact_id)
        text = "\n\n".join(unit.model().text for unit in units)
        path = f"chapters/{chapter.order_key}.md"
        if path in files:
            raise _violation(
                where=f"v2.export({project_id.value})",
                why=f"两个章节的顺序键都是 {chapter.order_key!r}，导出文件名冲突",
                fix="先修正章节顺序键使其唯一，再导出",
                order_key=chapter.order_key,
            )
        files[path] = text.encode("utf-8")
        chars += sum(1 for ch in text if not ch.isspace())
        entries.append(
            {
                "path": path,
                "sha256": _file_hash(files[path]),
                "kind": _ENTRY_CHAPTER,
                "artifact_id": entry.artifact_id,
                "title": chapter.title,
                "order_key": chapter.order_key,
                "narrative_chapter_id": chapter.narrative_chapter_id,
                "units": [
                    {
                        "unit_id": unit.artifact_id,
                        "sha256": _file_hash(unit.model().text.encode("utf-8")),
                    }
                    for unit in units
                ],
            }
        )

    for story_entry in _story_entries(snapshot):
        directory = _BIBLE_DIRS.get(story_entry["kind"])
        if directory is None:
            raise _violation(
                where=f"v2.export({project_id.value})",
                why=f"故事对象 kind {story_entry['kind']!r} 未登记导出目录",
                fix="在 v2_package_exporter._BIBLE_DIRS 显式登记该 kind 后重试",
                kind=story_entry["kind"],
            )
        path = f"bible/{directory}/{story_entry['artifact_id']}.yaml"
        payload = _yaml_bytes(
            {
                "artifact_id": story_entry["artifact_id"],
                "kind": story_entry["kind"],
                "content": story_entry["content"],
            }
        )
        files[path] = payload
        entries.append(
            {
                "path": path,
                "sha256": _file_hash(payload),
                "kind": story_entry["kind"],
                "artifact_id": story_entry["artifact_id"],
            }
        )

    relations = sorted(
        (
            {
                "source": rel.source.value,
                "relation_type": rel.relation_type,
                "target": rel.target.value,
                "metadata": dict(rel.metadata),
            }
            for rel in snapshot.relations
        ),
        key=lambda row: (row["source"], row["relation_type"], row["target"]),
    )
    files["relations.json"] = _json_bytes(relations)
    entries.append(
        {
            "path": "relations.json",
            "sha256": _file_hash(files["relations.json"]),
            "kind": _ENTRY_RELATIONS,
        }
    )

    entries.sort(key=lambda e: e["path"])
    manifest: dict[str, Any] = {
        "kind": EXPORT_KIND,
        "schema_version": EXPORT_SCHEMA_VERSION,
        "project_id": project_id.value,
        "project_name": project_name,
        "commit_id": commit_id.value,
        "counts": {
            "chapters": len(ms_catalog.chapters()),
            "story_entities": len(_story_entries(snapshot)),
            "relations": len(relations),
            "chars": chars,
        },
        "files": entries,
    }
    package_hash = package_hash_of(manifest)
    manifest["package_hash"] = package_hash
    files["manifest.json"] = _json_bytes(manifest)

    return ExportPackage(
        project_id=project_id.value,
        project_name=project_name,
        commit_id=commit_id.value,
        files=files,
        manifest=manifest,
        package_hash=package_hash,
    )


def _story_entries(snapshot: Any) -> list[dict[str, Any]]:
    """快照中的故事对象（kind + content），按 ID 排序（确定性）。"""
    from hastings.domain.story.catalog import StoryCatalog

    catalog = StoryCatalog.from_snapshot(snapshot)
    return [
        {"artifact_id": e.artifact_id, "kind": e.kind, "content": dict(e.content)}
        for e in catalog.entries
    ]


# --------------------------------------------------------------------------
# 包的磁盘 / zip 表示
# --------------------------------------------------------------------------
def write_package(package: ExportPackage, directory: Path) -> Path:
    """把包写到磁盘目录（已存在的同名文件覆盖）；返回目录路径。"""
    for rel, data in package.files.items():
        target = directory / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return directory


def read_package(directory: Path) -> dict[str, bytes]:
    """从磁盘目录读回包：{相对路径 posix: 字节}。"""
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def zip_package(package: ExportPackage) -> bytes:
    """确定性 zip：固定时间戳与属性，同包两次打 zip 字节一致。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in sorted(package.files):
            info = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            zf.writestr(info, package.files[rel])
    return buffer.getvalue()


# --------------------------------------------------------------------------
# 包校验与重导入
# --------------------------------------------------------------------------
def verify_package(files: dict[str, bytes]) -> dict[str, Any]:
    """完整性校验：逐文件 sha256 + 整包指纹 + schema 标识；通过则返回 manifest。"""
    manifest_raw = files.get("manifest.json")
    if manifest_raw is None:
        raise _violation(
            where="v2.export.verify",
            why="包内没有 manifest.json，不是 V2 导出包",
            fix="检查包来源；重导出后再试",
        )
    try:
        manifest: dict[str, Any] = json.loads(manifest_raw)
    except json.JSONDecodeError as exc:
        raise _violation(
            where="v2.export.verify",
            why=f"manifest.json 不是合法 JSON：{exc}",
            fix="包已损坏；从可靠备份恢复或重新导出",
        ) from exc
    if manifest.get("kind") != EXPORT_KIND:
        raise _violation(
            where="v2.export.verify",
            why=f"manifest.kind 是 {manifest.get('kind')!r}，不是 {EXPORT_KIND!r}",
            fix="确认这是 V2 导出包；旧格式备份不在此通道处理",
            kind=manifest.get("kind"),
        )
    for entry in manifest["files"]:
        data = files.get(entry["path"])
        if data is None:
            raise _violation(
                where=f"v2.export.verify({entry['path']})",
                why="manifest 登记的文件在包中缺失",
                fix="包不完整；从可靠备份恢复或重新导出",
                path=entry["path"],
            )
        actual = _file_hash(data)
        if actual != entry["sha256"]:
            raise _violation(
                where=f"v2.export.verify({entry['path']})",
                why=f"文件内容与 manifest 的 sha256 不符（期望 {entry['sha256'][:12]}…，"
                f"实际 {actual[:12]}…）",
                fix="包已被篡改或损坏；不要导入，从可靠备份恢复或重新导出",
                path=entry["path"],
            )
    actual_hash = package_hash_of(manifest)
    if actual_hash != manifest.get("package_hash"):
        raise _violation(
            where="v2.export.verify",
            why=f"整包指纹不符（期望 {str(manifest.get('package_hash'))[:12]}…，"
            f"实际 {actual_hash[:12]}…）",
            fix="manifest 与文件清单不一致；不要导入，重新导出",
        )
    return manifest


def _chapter_unit_ids_closure(entry: dict[str, Any]) -> Any:
    """按 manifest 登记的段落清单回放 unit_id：重导入保持段落身份不变。"""
    queue: list[str] = [str(unit["unit_id"]) for unit in entry["units"]]

    def new_id(kind: str) -> str:
        del kind
        if not queue:
            raise _violation(
                where=f"v2.export.import({entry['path']})",
                why="正文段落数超过 manifest 登记",
                fix="包正文与 manifest 不一致；重新导出",
                path=entry["path"],
            )
        return queue.pop(0)

    return new_id


def package_changes(
    files: dict[str, bytes],
) -> tuple[tuple[ArtifactChange, ...], tuple[RelationChange, ...]]:
    """把校验通过的包解析为可提交变化（章节 + 故事对象 + 关系）。"""
    manifest = verify_package(files)
    changes: list[ArtifactChange] = []

    for entry in manifest["files"]:
        if entry["kind"] == _ENTRY_CHAPTER:
            text = files[entry["path"]].decode("utf-8")
            units = split_paragraphs(text)
            registered = entry["units"]
            if len(units) != len(registered) or any(
                _file_hash(unit.encode("utf-8")) != meta["sha256"]
                for unit, meta in zip(units, registered, strict=False)
            ):
                raise _violation(
                    where=f"v2.export.import({entry['path']})",
                    why="正文分段或段落文本与 manifest 登记不一致（导出后正文可能被改动）",
                    fix="不要手工编辑包内文件；需要改正文请导出后在 V2 项目内编辑",
                    path=entry["path"],
                )
            change = import_chapter(
                ManuscriptCatalog(commit_id=None, entries=()),
                _chapter_unit_ids_closure(entry),
                chapter_id=entry["artifact_id"],
                title=entry["title"],
                text=text,
                order_key=entry["order_key"],
                narrative_chapter_id=entry.get("narrative_chapter_id"),
            )
            changes.extend(change.artifact_changes)
        elif entry["kind"] == _ENTRY_RELATIONS:
            continue
        else:
            payload = yaml.safe_load(files[entry["path"]])
            changes.append(
                ArtifactChange(
                    artifact_id=ArtifactId(payload["artifact_id"]),
                    content=payload["content"],
                    old_revision_id=None,
                    kind=payload["kind"],
                )
            )

    relations = tuple(
        RelationChange(
            action="add",
            source=ArtifactId(row["source"]),
            relation_type=row["relation_type"],
            target=ArtifactId(row["target"]),
            metadata=dict(row.get("metadata") or {}),
        )
        for row in json.loads(files["relations.json"])
    )
    return tuple(changes), relations


def apply_exported_package(
    reader: RepositoryReader,
    committer: CommitRepository,
    *,
    project_id: ProjectId,
    files: dict[str, bytes],
    actor: Actor,
    branch_name: str = "main",
    summary: str | None = None,
) -> ExportImportResult:
    """把导出包重导入为一次原子提交；幂等键绑定整包指纹。

    与迁移导入器同一套防线：先校验包完整性，确认目标分支存在且项目为空
    （重复导入命中幂等记录返回原结果），写入只经 Commit Service。

    **目标必须是另一个仓库实例里的空项目**：artifact ID 归属创建它的项目，
    同库内跨项目搬移 ID 会被仓库不变体拒绝（恢复到新作品需要 ID 重映射，
    0.3 不提供；备份用于同作品恢复，见 ``BackupService.restore``）。
    """
    manifest = verify_package(files)
    package_hash = manifest["package_hash"]
    key = f"v2-export:{EXPORT_SCHEMA_VERSION}:{package_hash}"
    where = f"v2.export.import({project_id.value})"

    existing = reader.find_commit_result(project_id, key)
    branch = reader.get_branch(project_id, branch_name)
    if branch is None:
        raise _violation(
            code=ec.REPO_BRANCH_NOT_FOUND,
            where=where,
            why=f"项目 {project_id.value} 下没有 {branch_name!r} 分支",
            fix="先用 create_project 建立项目与 main 分支，再导入包",
            project_id=project_id.value,
        )
    if existing is None and branch.head_commit_id is not None:
        raise _violation(
            code=ec.REPO_INVALID_CHANGE,
            where=where,
            why=f"目标项目非空（head={branch.head_commit_id.value}），包导入只写入空项目",
            fix="新建一个空项目再导入；重复导入同一包会命中幂等键并返回原结果",
            project_id=project_id.value,
        )

    commit: CommitResult
    if existing is not None:
        commit = existing
    else:
        changes, relations = package_changes(files)
        commit = committer.commit(
            CommitRequest(
                project_id=project_id,
                branch_id=branch.id,
                expected_head=branch.head_commit_id,
                actor=actor,
                idempotency_key=key,
                summary=summary or f"导入 V2 导出包（{manifest['project_name']}）",
                changes=changes,
                relations=relations,
                events=(
                    (
                        _EVENT_PACKAGE_IMPORTED,
                        {
                            "project_name": manifest["project_name"],
                            "source_commit_id": manifest["commit_id"],
                            "package_hash": package_hash,
                            "schema_version": manifest["schema_version"],
                            "files": len(manifest["files"]),
                            "chapters": manifest["counts"]["chapters"],
                        },
                    ),
                ),
            )
        )
    return ExportImportResult(
        status=commit.status,
        commit_id=commit.commit_id.value if commit.commit_id else None,
        new_head=commit.new_head.value if commit.new_head else None,
        replayed=existing is not None,
    )


def _violation(
    *, where: str, why: str, fix: str, code: str = "EXPORT_PACKAGE_INVALID", **details: Any
) -> ViolationError:
    return ViolationError(code=code, where=where, why=why, fix=fix, details=details)


def unzip_package(data: bytes) -> dict[str, bytes]:
    """把导出 zip 解回文件字典；不是 zip 或条目被恶意构造时显式拒绝。

    与 ``zip_package`` 对称：恢复路径从「拿到的字节」开始，而不是从磁盘目录，
    因此必须自己拒绝目录穿越条目（``../`` 与绝对路径）——备份文件可能来自
    任何地方，猜它没被构造过等于没有校验。
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = [info.filename for info in zf.infolist() if not info.is_dir()]
            files = {name: zf.read(name) for name in names}
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        raise _violation(
            where="v2.export.unzip",
            why=f"上传内容不是可读的 V2 导出 zip：{exc}",
            fix="重新从「导出」下载备份包；不要手工改名或解压再压缩",
        ) from exc
    for name in files:
        pure = name.replace("\\", "/")
        if pure.startswith("/") or ".." in pure.split("/"):
            raise _violation(
                where="v2.export.unzip",
                why=f"包内路径 {name!r} 不是包内相对路径",
                fix="备份包只含 manifest 登记的相对路径；不要导入被构造过的包",
                path=name,
            )
    if "manifest.json" not in files:
        raise _violation(
            where="v2.export.unzip",
            why="zip 里没有 manifest.json，不是 V2 导出包",
            fix="确认下载来源；重新导出后再恢复",
        )
    return files

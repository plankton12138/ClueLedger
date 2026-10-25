"""作品装配服务：把库里的精确版本装进作品（0.8 D）。

装配只改作品 ``.clueledger/assembly.json``：记录 library/module/revision 与简单关联，
不复制任何模块原文。装、卸、升级都要拿出确定的版本；库里发布了新版不会自动跟随，
由作者显式升级。

几条边界：

- **一次操作一次 CAS**：调用方带上读到的装配指纹，别人先改过就冲突，不覆盖新绑定。
- **同一实例只绑一个版本**：需要两种安排时另存变体，系统不做「同实例双版本」。
- **依赖缺失只提示**：模块被删、版本缺失、库失联时，绑定保留并标出问题；正文仍可读，
  不自动到库里找最新版顶替。
- **发布与绑定分开**：「保存并用于本作品」先发布新版本，再尝试更新绑定；绑定失败时保留
  已发布版本并报告，重试不会重复发布。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path

from hastings.library.ids import new_id
from hastings.library.modules import ModuleInfo, ModuleLibrary
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.module_assembly import AssemblyStore, Binding, ModuleRef

__all__ = ["AssemblyService"]


class AssemblyService:
    """一个作品的装配：读诊断、装卸、升级与「保存并用于本作品」。"""

    def __init__(self, library: ModuleLibrary, workspace: Workspace) -> None:
        self.library = library
        self.workspace = workspace
        self.store = AssemblyStore(Path(workspace.root))

    # -- 读取 ---------------------------------------------------------------

    def view(self) -> dict[str, object]:
        """当前装配与依赖诊断；绑定项保留，问题只作标记。"""
        assembly = self.store.read()
        rows: list[dict[str, object]] = []
        for binding in assembly.bindings:
            rows.append({**binding.view(), **self._diagnose(binding.module)})
        return {
            "schema_version": assembly.schema_version,
            "library_id": assembly.library_id,
            "digest": self.store.digest(),
            "bindings": rows,
            "links": [list(link) for link in assembly.links],
        }

    def _diagnose(self, ref: ModuleRef) -> dict[str, object]:
        library_id = self.library.library_id
        if library_id is None:
            return {"kind": "", "name": "", "deleted": False, "problems": ["library_missing"]}
        if ref.library_id != library_id:
            return {"kind": "", "name": "", "deleted": False, "problems": ["library_mismatch"]}
        try:
            module = self.library.module(ref.module_id)
        except WorkspaceError:
            return {"kind": "", "name": "", "deleted": False, "problems": ["module_missing"]}
        problems: list[str] = []
        if not self.library.storage.revision_exists(ref.module_id, ref.revision_id):
            problems.append("revision_missing")
        if module.deleted:
            problems.append("module_trashed")
        return {
            "kind": module.kind,
            "name": module.name,
            "deleted": module.deleted,
            "problems": problems,
        }

    # -- 写入 ---------------------------------------------------------------

    def bind(
        self,
        *,
        module_id: str,
        revision_id: str,
        label: str = "",
        expected_digest: str | None,
        request_key: str,
    ) -> dict[str, object]:
        assembly = self.store.read()
        binding = Binding(
            id=new_id("binding"),
            module=self._checked_ref(module_id, revision_id),
            label=label.strip(),
        )
        return self._commit(
            [*assembly.bindings, binding],
            links=assembly.links,
            expected_digest=expected_digest,
            request_key=request_key,
        )

    def unbind(
        self, *, binding_id: str, expected_digest: str | None, request_key: str
    ) -> dict[str, object]:
        assembly = self.store.read()
        remaining = [item for item in assembly.bindings if item.id != binding_id]
        if len(remaining) == len(assembly.bindings):
            raise WorkspaceError(
                code="ASSEMBLY_BINDING_NOT_FOUND",
                where="assembly.unbind",
                why=f"这个作品没有绑定：{binding_id}",
                fix="刷新装配列表后重试；不要按模块名猜绑定 ID",
                details={"binding_id": binding_id},
            )
        return self._commit(
            remaining,
            links=assembly.links,
            expected_digest=expected_digest,
            request_key=request_key,
        )

    def upgrade(
        self,
        *,
        binding_id: str,
        revision_id: str,
        expected_digest: str | None,
        request_key: str,
    ) -> dict[str, object]:
        assembly = self.store.read()
        updated: list[Binding] = []
        found = False
        for item in assembly.bindings:
            if item.id != binding_id:
                updated.append(item)
                continue
            found = True
            updated.append(
                Binding(
                    id=item.id,
                    module=self._checked_ref(item.module.module_id, revision_id),
                    label=item.label,
                )
            )
        if not found:
            raise WorkspaceError(
                code="ASSEMBLY_BINDING_NOT_FOUND",
                where="assembly.upgrade",
                why=f"这个作品没有绑定：{binding_id}",
                fix="刷新装配列表后重试",
                details={"binding_id": binding_id},
            )
        return self._commit(
            updated,
            links=assembly.links,
            expected_digest=expected_digest,
            request_key=request_key,
        )

    def reorder(
        self,
        *,
        binding_ids: Sequence[str],
        expected_digest: str | None,
        request_key: str,
    ) -> dict[str, object]:
        assembly = self.store.read()
        by_id = {item.id: item for item in assembly.bindings}
        if sorted(binding_ids) != sorted(by_id):
            raise WorkspaceError(
                code="ASSEMBLY_INVALID",
                where="assembly.reorder",
                why="排序请求与当前绑定不一致",
                fix="提交当前全部绑定 ID 的一个排列；不要漏掉或新增绑定",
                details={"given": list(binding_ids), "current": sorted(by_id)},
            )
        return self._commit(
            [by_id[binding_id] for binding_id in binding_ids],
            links=assembly.links,
            expected_digest=expected_digest,
            request_key=request_key,
        )

    def replace(
        self,
        *,
        bindings: Sequence[Binding],
        links: Sequence[tuple[str, str]] = (),
        expected_digest: str | None,
        request_key: str,
    ) -> dict[str, object]:
        return self._commit(
            bindings, links=links, expected_digest=expected_digest, request_key=request_key
        )

    def publish_and_bind(
        self,
        *,
        module_id: str,
        base_revision: str | None,
        expected_head: str | None,
        entries: Sequence[Mapping[str, object]],
        expected_digest: str | None,
        request_key: str,
    ) -> dict[str, object]:
        """保存并用于本作品：先发布，再绑定；两步结果分别返回。"""
        published: dict[str, object] | None = None
        publish_error: WorkspaceError | None = None
        try:
            revision = self.library.publish(
                module_id=module_id,
                base_revision=base_revision,
                expected_head=expected_head,
                entries=entries,
                request_key=f"{request_key}:publish",
            )
            published = revision.view()
        except WorkspaceError as exc:
            publish_error = exc

        if published is None:
            assert publish_error is not None
            return {
                "published": False,
                "module": None,
                "binding_updated": False,
                "error": _error_view(publish_error),
            }

        module_ref = ModuleRef(
            library_id=self.library.library_id or "",
            module_id=module_id,
            revision_id=str(published["revision_id"]),
        )
        try:
            assembly = self.store.read()
            existing = next(
                (item for item in assembly.bindings if item.module.module_id == module_id),
                None,
            )
            bindings = [item for item in assembly.bindings if item.module.module_id != module_id]
            bindings.append(
                Binding(
                    id=existing.id if existing is not None else new_id("binding"),
                    module=module_ref,
                    label=existing.label if existing is not None else "",
                )
            )
            result = self._commit(
                bindings,
                links=assembly.links,
                expected_digest=expected_digest,
                request_key=f"{request_key}:bind",
            )
            return {
                "published": True,
                "module": module_ref.view(),
                "binding_updated": True,
                "assembly": result,
            }
        except WorkspaceError as exc:
            return {
                "published": True,
                "module": module_ref.view(),
                "binding_updated": False,
                "error": _error_view(exc),
            }

    def save_content(
        self,
        *,
        binding_id: str,
        module_id: str,
        base_revision: str,
        name: str,
        entries: Sequence[Mapping[str, object]],
        expected_digest: str | None,
        request_key: str,
    ) -> dict[str, object]:
        """只更新本作品使用的版本与名称，不发布到库的最新内容。"""
        assembly = self.store.read()
        target = assembly.binding(binding_id)
        if target is None or target.module.module_id != module_id:
            raise WorkspaceError(
                code="ASSEMBLY_TARGET_CONFLICT",
                where="assembly.content",
                why="这个模块的作品绑定已变化或已被移除",
                fix="重新读取作品模块后再保存；未保存内容仍可保留",
            )
        if target.module.library_id != self.library.library_id:
            raise WorkspaceError(
                code="LIBRARY_MISMATCH",
                where="assembly.content",
                why="作品挂载的模块来自另一个模块库",
                fix="重新连接原模块库后再保存，保留当前草稿",
            )
        label = name.strip()
        if not label or len(label) > 80:
            raise WorkspaceError(
                code="MODULE_NAME_INVALID",
                where="assembly.content",
                why="模块名称需要为 1–80 个字符",
                fix="缩短名称；详细内容写在正文里",
            )
        if not 8 <= len(request_key) <= 120:
            raise WorkspaceError(
                code="REQUEST_KEY_INVALID",
                where="assembly.content",
                why="请求键长度需要在 8–120 字符之间",
                fix="为这次保存生成稳定且不重复的请求键",
            )
        token = hashlib.sha256(
            f"{self.workspace.root}:{binding_id}:{request_key}".encode()
        ).hexdigest()
        revision = self.library.save_workspace_revision(
            module_id=module_id,
            base_revision=base_revision,
            entries=entries,
            request_key=f"workspace-{token}",
        )
        if target.module.revision_id == revision.revision_id and target.label == label:
            return self.view()
        if target.module.revision_id != base_revision:
            raise WorkspaceError(
                code="ASSEMBLY_STALE_CONFLICT",
                where="assembly.content",
                why="作品正在使用的内容版本已变化",
                fix="保留草稿，重新读取当前版本后再保存",
            )
        ref = self._checked_ref(module_id, revision.revision_id)
        return self._commit(
            [
                Binding(id=item.id, module=ref, label=label) if item.id == binding_id else item
                for item in assembly.bindings
            ],
            links=assembly.links,
            expected_digest=expected_digest,
            request_key=f"content-{token}",
        )

    # -- 内部 ---------------------------------------------------------------

    def _commit(
        self,
        bindings: Sequence[Binding],
        *,
        links: Sequence[tuple[str, str]],
        expected_digest: str | None,
        request_key: str,
    ) -> dict[str, object]:
        library_id = self.library.library_id
        if library_id is None:
            raise WorkspaceError(
                code="LIBRARY_NOT_FOUND",
                where="assembly.commit",
                why="还没有模块库，无法绑定模块",
                fix="先在模块库里新建一份内容，再装入作品",
            )
        assembly = self.store.replace(
            library_id=library_id,
            bindings=bindings,
            links=links,
            expected_digest=expected_digest,
            request_key=request_key,
        )
        return {
            "schema_version": assembly.schema_version,
            "library_id": assembly.library_id,
            "digest": self.store.digest(),
            "bindings": [
                {**item.view(), **self._diagnose(item.module)} for item in assembly.bindings
            ],
            "links": [list(link) for link in assembly.links],
        }

    def _checked_ref(self, module_id: str, revision_id: str) -> ModuleRef:
        library_id = self.library.library_id
        if library_id is None:
            raise WorkspaceError(
                code="LIBRARY_NOT_FOUND",
                where="assembly.bind",
                why="还没有模块库，无法绑定模块",
                fix="先在模块库里新建一份内容，再装入作品",
            )
        module: ModuleInfo = self.library.module(module_id)
        if not self.library.storage.revision_exists(module_id, revision_id):
            raise WorkspaceError(
                code="MODULE_REVISION_NOT_FOUND",
                where=f"assembly.bind:{module_id}",
                why=f"这个模块没有版本 {revision_id}",
                fix="刷新版本列表；不要用最新版顶替指定版本",
                details={"module_id": module_id, "revision_id": revision_id},
            )
        return ModuleRef(library_id=library_id, module_id=module.module_id, revision_id=revision_id)


def _error_view(exc: WorkspaceError) -> dict[str, object]:
    return {
        "code": exc.code,
        "where": exc.where,
        "why": exc.why,
        "fix": exc.fix,
        "details": dict(exc.details),
    }

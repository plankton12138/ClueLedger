"""按作品共享的写作服务：模型任务、压缩与构思采用都取同一份实例。

同一部作品若存在两个 ``TurnService``，两边的「当前正在进行」、代次撤销与操作登记
互不知情：从一个入口暂停，另一个入口仍在写；操作接口也可能读到不同的实例。这里
只建一次，写作路由、构思采用与操作接口都从这里取。

服务按需创建；作品删除或改名后丢弃缓存，下一次请求按新路径重建。
"""

from __future__ import annotations

import threading

from hastings.library.modules import ModuleLibrary
from hastings.workspace.activity import ActivityStore
from hastings.workspace.compaction_ops import CompactionRunner
from hastings.workspace.operations import OperationStore
from hastings.workspace.run_registry import RunRegistry
from hastings.workspace.service import ProviderFactory, TurnService
from hastings.workspace.state import ConversationStore, RecentWorkspaces, conversation_root

__all__ = ["WorkspaceServices"]


class WorkspaceServices:
    """作品级服务的唯一来源；``turns`` 同时供操作接口按标识查找。"""

    def __init__(
        self,
        workspaces: RecentWorkspaces,
        provider_factory: ProviderFactory,
        *,
        library: ModuleLibrary | None = None,
        run_registry: RunRegistry | None = None,
        operations: OperationStore | None = None,
        turns: dict[str, TurnService] | None = None,
    ) -> None:
        self.workspaces = workspaces
        self.provider_factory = provider_factory
        self.library = library
        self.run_registry = run_registry
        self.operations = operations
        #: 与操作接口共享：按作品标识直接取到同一个轮次服务。
        self.turns: dict[str, TurnService] = turns if turns is not None else {}
        self.runners: dict[str, CompactionRunner] = {}
        self.lock = threading.RLock()
        self._activity: dict[str, ActivityStore] = {}

    # -- 读写服务 -----------------------------------------------------------

    def activity_for(self, workspace_id: str) -> ActivityStore:
        self.workspaces.get(workspace_id)
        with self.lock:
            store = self._activity.get(workspace_id)
            if store is None:
                store = ActivityStore(conversation_root(self.workspaces.root, workspace_id))
                store.recover()
                self._activity[workspace_id] = store
            return store

    def service_for(self, workspace_id: str) -> TurnService:
        binding = self.workspaces.get(workspace_id)
        cached = self.turns.get(workspace_id)
        if cached is not None and str(cached.workspace.root) == binding.path:
            # 压缩接口不能依赖「谁先访问」：缓存命中时也要保证运行器已建好。
            if workspace_id not in self.runners:
                self.runners[workspace_id] = self._runner_for(cached)
            return cached
        workspace = self.workspaces.workspace(workspace_id)
        store = ConversationStore(conversation_root(self.workspaces.root, workspace_id))
        service = TurnService(
            workspace,
            store,
            self.provider_factory,
            library=self.library,
            run_registry=self.run_registry,
            workspace_id=workspace_id,
            operations=self.operations,
        )
        service.activity = self.activity_for(workspace_id)
        # 进程重启后，没有正常结束的轮次在这里补一条中断记录；不自动继续。
        service.mark_interrupted()
        self.runners[workspace_id] = self._runner_for(service)
        self.turns[workspace_id] = service
        return service

    def runner_for(self, workspace_id: str) -> CompactionRunner:
        self.service_for(workspace_id)
        return self.runners[workspace_id]

    def _runner_for(self, service: TurnService) -> CompactionRunner:
        runner = CompactionRunner(service)
        runner.recover()
        return runner

    def forget(self, workspace_id: str) -> None:
        """目录删除或改名后丢弃旧缓存：服务绑定的是旧路径与旧会话目录。"""
        with self.lock:
            self.turns.pop(workspace_id, None)
            self.runners.pop(workspace_id, None)
            self._activity.pop(workspace_id, None)

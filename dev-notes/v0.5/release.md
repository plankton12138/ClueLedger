# 0.5 运行与验证

当前包版本为 `0.5.0`。人物小传位于 `设定集/角色小传/姓名.md`，接入目录、搜索、只读阅读、文件工具、Git 和模型按需读取。目录与接口以[当前模块说明](modules/README.md)为准。

## 运行

在仓库根目录执行：

```powershell
uv run python -m hastings.server
# 或启动时预先打开作品
uv run python -m hastings.server --workspace <作品目录>
```

前端开发在 `frontend/` 执行 `npm run dev`，生产构建执行 `npm run build` 后由后端托管。模型连接在应用设置中配置；已有连接、凭据和会话机制沿用，不使用进程环境中的模型 Key。新建作品提供空角色目录以及线索、真相两份空文件。旧版汇总小传无兼容或迁移入口。

## 本轮验证

2026-10-21，本次工作区代码验证结果如下；历史验证不计入本次结果。

| 检查 | 结果与证据范围 |
|---|---|
| `uv run pytest tests/workspace -q` | 276 通过；含角色文件操作、路径边界、工具调用、HTTP、搜索、Git 记录和撤销，以及既有工作区回归 |
| `npm test -- --no-file-parallelism`（frontend） | 8 个文件、67 项通过；含角色刷新保持选择、改名后清除旧路径 |
| `npm run test:e2e`（frontend，独立端口 8947） | 构建前端并启动真实后端；11 项浏览器测试通过，含角色分组、读取、搜索与既有布局和历史流程 |
| `uv run ruff check src/hastings tests/workspace` | 通过 |
| `uv run mypy src/hastings` | 36 个源文件通过 |

Python 回归出现一条第三方 TestClient 弃用警告，不影响测试结果。前端测试使用串行执行，避免本机曾出现的临时目录并发问题。

## 当前限制与未验证项

角色修改复用现有文件工具；本次没有调用真实供应商验证模型是否遵循“先读小传”的提示，没有执行桌面安装包构建。现有模型连接和会话的历史真实验证不能替代本次角色行为验证。

角色改名不自动同步标题或正文人名，没有自动合并人物、人物关系图或文件监听。流式、对话 Markdown、思考过程等新体验仍处于[计划](chat-experience-plan.md)，不属于本轮交付。

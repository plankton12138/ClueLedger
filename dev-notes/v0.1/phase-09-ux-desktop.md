# Phase 9 构建笔记：前端体验修补与桌面化

> 对应计划：[phase-09-ux-desktop-plan.md](phase-09-ux-desktop-plan.md)
> 周期：2026-10-10 ~ 2026-10-11。任务 T1–T11 全部交付，DoD 八条逐条验收（见 §7）。
> 关键词：体验修补、选项协议、新会话、响应式抽屉、Electron 壳、PyInstaller/NSIS 打包。

## 1. 交付内容

交付链（M1→M2→M3，12 次原子提交）：`9d42712`（T1 对话详略协议 + `【选项】` 标记）/ `2bcbb65`（T4 parseOptions 解析）→ `73b71e2`（T2 ThinkingCard 折叠）→ `809a5da`（T3 首字等待占位 + stage 建议按钮）→ `8282f39`（T5 session/reset 归档 + 留痕）→ `2458b97`（T6 新会话入口 + 重连快照重放）→ `01b079d`（T7 横向溢出修复）→ `22076bc`（T8 <lg 对话栏抽屉化 + layout.ts 断点事实源）→ `8af32a9`（T9 后端桌面参数 --static-dir/--config-dir）→ `5379fd5`（pyinstaller 入 dev 依赖）→ `d4fa696`（T10 Electron 骨架：动态端口 spawn sidecar + 崩溃错误页 + IPC 重启）→ `5d7436a`（T11 PyInstaller spec + electron-builder NSIS）。

产物（不入库，gitignore）：

- sidecar：`build-resources/sidecar/hastings-server.exe`（onefile，由 [packaging/hastings-server.spec](../../packaging/hastings-server.spec) 构建）；
- 安装包：`desktop/dist/Hastings-Setup-0.1.0.exe`（180.6 MB，含 Electron 33 + sidecar + 前端 dist）。

复现命令：

```powershell
# 1) 前端产物
cd frontend; npm run build
# 2) sidecar（仓库根）
uv run pyinstaller packaging/hastings-server.spec `
  --distpath build-resources/sidecar --workpath build/pyinstaller --noconfirm
# 3) 安装包（NSIS 二进制走镜像）
cd desktop
$env:ELECTRON_BUILDER_BINARIES_MIRROR="https://npmmirror.com/mirrors/electron-builder-binaries/"
npm run dist   # = tsc + electron-builder --win --x64
```

## 2. 关键决策（D1–D10 落地与偏离）

- **D1–D5 全部按计划落地**：折叠是纯渲染层改造（reducer/事件契约零改动）；选项协议是 prompt 软约束 + 前端宽容解析；reset 走 REST 不开 WS 新帧；占位条件与 stage 建议均为可单测纯函数。
- **D6 抽屉**：断点事实源升级为 `matchMedia("(min-width:1024px)")` 常驻监听（计划只要求搜索跳转同款，实现更进一步——窗口拖动跨断点时布局实时翻转，无需重挂载）。四宽度（480/790/1024/1280）CDP 实测无横向滚动。
- **D7 静态托管**：`--static-dir` 成为本阶段唯一为桌面化动的后端业务代码；前端 API base 零改动（同源相对路径在计划 §0 已核实），兑现"桌面壳直接复用 Web 产物"。
- **D8 偏离一处（增强）**：计划写「v1 不需要 IPC（preload 留最小占位）」。实现中崩溃错误页的「重启写作台」按钮必须回调主进程，故补了一个 `sidecar:restart` IPC handle（[main.ts:184](../../desktop/main.ts#L184)），preload 暴露 `window.hastings.restart()`——仍是最小 IPC 面，无文件系统/命令通道。另一处实现选择：sidecar `exit` 后主进程**直接 loadFile 错误页**，不经 preload 事件转发给 React，避免"崩溃时前端自身也不可用"的死锁。
- **D9 打包顺序生效**：spike 前置到 M3 第一件事，一次试出 5 个 hidden imports（`uvicorn.logging / loops.auto / protocols.http.auto / protocols.websockets.auto / lifespan.on`），pydantic v2 与 websockets 由 PyInstaller hooks 自动收集，spec 无需 collect_all。
- **D10 偏离一处（修正）**：计划钉死数据落 `%APPDATA%/Hastings`，但 Electron 默认 userData 取 package.json 的 `name`（实际落成 `hastings-desktop`）。修正方式是 ready 前 `app.setName("Hastings")`（[main.ts:27](../../desktop/main.ts#L27)）；并在 spawn 前 `mkdirSync` 自建 config/stories/corpus——冒烟证明空 userData 首跑即三目录就位。

## 3. Agent 设计经验（本阶段最重要的教训）

P9 一半工作在 Agent 交互面（prompt/选项/会话），一半在桌面工程。四透镜在本阶段的形态发生了变化：**透镜的消费方从"模型"扩展到了"用户"与"打包后的进程边界"，但判据不变。**

### 3.1 错误消息是喂给模型的 API——打包后，下游变成了用户

T9 新增的每一个失败出口都按三要素写：`--config` 与 `--config-dir` 互斥 → 退出码 2 +「哪里错/为什么错/怎样算对」；`--static-dir` 不存在 → 退出码 1 同样三要素。字段级定位原样透传的老规矩在 WS 层继续生效（命令帧 pydantic 校验错误进 error 帧，[ws.py:142](../../src/hastings/server/ws.py#L142)）。

桌面壳里这条透镜的新含义：**sidecar 是主进程的"模型"——它返回的唯一结构化信息是退出码和健康探针，主进程必须把它翻译成人话**。错误页展示「本地写作后端意外退出（退出码 1）。」而不是裸弹窗或静默白屏，并且带恢复动作（重启按钮）。教训也留了一条缺口：GUI 打包态 sidecar 的 stdout/stderr 没有可视落点（[main.ts:75-76](../../desktop/main.ts#L75-L76) 只转发给主进程 stdio，双击启动时无人接收），崩溃原因的可诊断性弱于 dev——这是下一批桌面工作应当按"错误即接口"标准补齐的（见 §8）。

### 3.2 硬约束放工具层，prompt 只负责解释世界——本阶段恰好划出了这条线的边界

A2（≤300 字、≤2 问）**只能**放 prompt：自由文本产出无法在工具层判定，接受残余不遵循。A3（选项块）则是混合体：格式协议放 prompt 软引导，但"格式漂移不能搞坏界面"是硬约束，落在前端解析层——`parseOptions` 残缺即原样渲染。

真正的硬约束全部在机器可判定层，且一个都没上浮到 prompt：

- 写作流运行中禁止 reset → 后端 409（前端确认框文案只是解释，不是执行点）；
- config 参数互斥、静态目录必须存在 → 后端 argparse/启动校验；
- 对话抽屉/常驻栏的形态选择 → matchMedia 断点事实源，不信任 CSS 与 JS 各自猜测；
- sidecar 只绑 127.0.0.1、端口由主进程先占再传 → 桌面层独占资源管理，后端不猜。

prompt 文本只负责"解释这个世界期望怎样"，每一条软约定都能在硬层找到兜底，这是本阶段最可复用的手感。

### 3.3 泄压阀——三情形在 UI 与进程层的对应表

| 情形 | 选项渲染（T4） | 桌面壳（T10/T11） |
|---|---|---|
| 违规（输入不合法） | `【选项】` 块存在但 A/B/C 残缺 → 不当选项渲染，正文原样输出 | `--config/--config-dir` 同传、slug 坏 WS 连接 1008 拒绝 |
| 合法但空结果 | 模型没输出标记块（绝大多数回复）→ 正常文本，无按钮区 | 端口被占 → pickPort 天然避开另选；无 config.yaml → 回落 .env 单档案（P6 既有泄压路径） |
| 环境故障 | WS 断连 → 既有重连 + 快照重放，不靠按钮状态 | sidecar exit / 健康 30s 超时 / exe 缺失 → 错误页 + 重启 IPC；退出 taskkill /T 兜底 uvicorn 子进程 |

关键手法：**三种情形的出口在代码里必须长得不一样**。T10 初版曾考虑崩溃时让 preload 转发事件给 React 渲染错误态——被否决：环境故障时不能假设前端渲染管线还活着，主进程直接 loadFile 静态错误页，这是"环境故障"出口与另两种情形的物理隔离。

### 3.4 可观测性是事件契约的一部分——不改帧，只增加读取者

本阶段 DoD 红线之一是"不改事件契约"，最终零新增 WS 事件帧。所有新 UI 能力都是对既有帧流的**新读取者**：ThinkingCard 读 thinking buffer、占位读 running 态 + 工具卡状态、选项按钮读 assistant 文本。reset 是管理动作，不发明帧，而是进 events.jsonl 留痕——管理动作必须可审计、且走与创作事件同一条观测通道（T5 测试钉了留痕断言）。

桌面化新增的观测面同样遵循"先有契约再消费"：主进程对 sidecar 的全部健康认知只有一个 `GET /api/health` 契约（400ms 轮询、30s 上限、末次错误透传），不解析日志、不猜启动进度。最终安装包冒烟用逐帧时间戳证明契约在 PyInstaller + onefile + 动态端口的新拓扑下原样成立：真模型 TTFT 766ms，`agent_start → message_start → message_update* → message_end → turn_end → agent_end` 帧序完整（§6 有存档）。

## 4. 评审记录

本阶段未启用 TRAE-code-review 双验证者（UI/壳工程为主，tau 内核零 diff），评审方式为：七道质量门 + 三层端到端冒烟（dev 模式 CDP、sidecar 模式 CDP、干净安装包真模型）。质量门数据见 §6。

## 5. 踩过的坑

1. **spec 的相对路径相对 spec 文件，不相对 cwd**：`Analysis(["packaging/sidecar_entry.py"])` 在仓库根执行会被解析成 `packaging/packaging/...`。改为相对 spec 所在目录的 `sidecar_entry.py`。
2. **distpath 不是 Analysis 参数**：是 CLI 选项，误塞进 Analysis 构造参数。
3. **tsconfig target 必须 ES2022**：写成 ES22 报 TS6046。
4. **Electron userData 取 package name 不是 productName**：靠冒烟时发现目录名 `hastings-desktop` 才抓到，`app.setName` 必须在 ready 与任何 getPath 之前。
5. **CDP 开关时序**：`remote-debugging-port` 必须在 app.whenReady 前 `appendSwitch`。
6. **electron 二进制与 NSIS 资源下载**：npm 镜像环境下需分别设 `ELECTRON_MIRROR`（install.js 重拉）与 `ELECTRON_BUILDER_BINARIES_MIRROR`（winCodeSign/nsis/nsis-resources）。
7. **desktop/node_modules 无既有 ignore 规则覆盖**：根 `dist/` 能覆盖 desktop/dist 但管不了 node_modules，需 desktop/.gitignore。
8. **非交互会话里 taskkill 无 /F 无法关闭 GUI 窗口**（exit 128 但进程还在）：冒烟改用 Win32 `PostMessage(WM_CLOSE)` 模拟用户点 X——这反而更接近真实退出路径，验证了 before-quit 异步清理。
9. **WS 冒烟脚本的帧形状误判**：事件帧是顶层 `{"type":"message_update",...}` 而非 `{"type":"event","event":{...}}` 包装；且 prompt 的 `response(started)` 是立即 ack，不能当作终态帧 break。以事件存储（GET /events）对账后澄清，逐帧打印脚本存档在 Temp 探针目录 `ws_pack_smoke.py`。

## 6. 验证输出存档

**七道质量门（2026-10-11 收尾复跑）**：

| 门 | 结果 |
|---|---|
| pytest | 641 passed（36.3s） |
| ruff check . | All checks passed（169 files） |
| ruff format --check . | 169 files already formatted |
| mypy（配置包 tau_ai/tau_agent/hastings） | Success: no issues in 78 source files |
| 前端 tsc --noEmit | exit 0 |
| vitest | 25 files / 248 tests passed |
| vite build | 成功（index 438.36 kB / gzip 135.97 kB） |

**T10 冒烟（dev + sidecar 双模式，CDP 自动化）**：

- dev：`HASTINGS_DEV_URL=http://localhost:5173/?w=ollama-verify HASTINGS_CDP=9226`，title/连接态/空态建议/新会话按钮全部正确；
- sidecar：env 指向 spike exe + frontend/dist + 独立临时数据目录，动态端口 59036 健康 OK、托管前端、空 stories 落「文章」页；
- 崩溃恢复：`taskkill /IM hastings-server.exe /T /F` → 窗口自动切错误页显示退出码 1 → CDP 点「重启写作台」→ IPC 重启同端口、页面恢复；
- 退出：electron 与 hastings-server 进程全部消失，监听端口清零。

**T11 干净安装包冒烟（Hastings-Setup-0.1.0.exe）**：

- 卸载旧版 + 清空 `%APPDATA%\Hastings` 后静默安装到独立路径（`/S /D=...`），resources 内 sidecar/frontend-dist/app.asar 齐备；
- 首跑：窗口标题「黑斯廷斯备忘录 · 会记账的悬疑小说 AI 主编」，userData 自动生成 config/stories/corpus 三目录，sidecar 动态端口（本次 63689）`/api/health` 200、`/` 托管 index 200；
- 真模型对话：POST 创建 smoke-chat 工作区 → WS `/ws/smoke-chat` 发 prompt → 收到完整流式帧序，模型回「你好，我是 Hastings——你的悬疑小说主编，先从雨夜火车站这场戏的谜面聊起吧。」（deepseek-flash，TTFT 766ms）；GET /events 与 WS 帧序对账一致；
- 退出：PostMessage WM_CLOSE 模拟点 X → 8 秒内 Hastings/hastings-server 进程数 0、端口释放（before-quit → taskkill /T /F 路径验证）。

## 7. DoD 对照（计划 §1 八条）

1. **思考折叠** ✅ T2 `73b71e2`：三处接线 + 「思路 · N 字」/「思考中…」摘要，vitest 钉摘要纯函数。
2. **对话协议** ✅ T1 `9d42712`：详略约定 + intake 1–2 问按影响排序 + `【选项】` 协议与 few-shot，prompt 测试全量更新。
3. **选项承接** ✅ T4 `2bcbb65`：尾部块解析、点按发送带文案、残缺/无标记原样渲染（泄压阀有测试）。
4. **新会话** ✅ T5+T6 `8282f39`/`2458b97`：归档时间戳副本、running 409、events.jsonl 留痕、前端确认 + 重连空快照；归档不进列表（移交）。
5. **等待与空态** ✅ T3 `809a5da`：占位互斥条件纯函数 + stage→建议纯函数，均有 vitest。
6. **响应式** ✅ T7+T8 `01b079d`/`22076bc`：480/790/1024/1280 四宽度 CDP 实测零横向滚动，抽屉开合与遮罩关闭正常，lg+ 布局零回归。
7. **桌面化** ✅ T9–T11：NSIS 安装包、自动拉起 sidecar（动态端口）、健康就绪再加载、数据落 `%APPDATA%\Hastings`、WM_CLOSE 零残留、崩溃错误页 + 重启、Web dev 形态照旧（T10 dev 冒烟同场验证）。
8. **红线** ✅：`git diff 3946ca3..HEAD -- src/tau_ai src/tau_agent` 为空；hastings 零 import tau_coding；七门全绿（§6）；本笔记 §3 四透镜专节；DESIGN.md §8 顺延（Phase 1–10/11 编号 + §5.1 桌面交付形态行，立项时改动，随本阶段 docs 提交）。

## 8. 已知留白（转入后续阶段）

- **sidecar 打包态日志无可视落点**：GUI 双击时 stdio 转发无人接收，崩溃页仅有退出码。后续应落 `userData/logs/sidecar.log` 并在错误页给「打开日志目录」；配置缺失类 boot 失败值得三要素文案（现在依赖 sidecar stderr）。
- RAG 重依赖（torch/BGE-M3）打包策略：P10 立项首要决策（当前 180.6 MB 包体尚无它们）。
- mac/linux 打包、代码签名、electron-updater 自动更新、应用图标（现为 Electron 默认图标）。
- 归档会话（session-\<ts\>.jsonl）的列表/切换 UI。
- 桌面多窗口/菜单/托盘、window-state 持久化。
- v1 无升级数据迁移：旧 cwd 形态 stories/ 需手工拷入 `%APPDATA%\Hastings\stories`。

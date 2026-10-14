# 构建笔记：E2E 测试基建（Playwright × 单进程真服务）

> 方案：[e2e-test-plan.md](e2e-test-plan.md)。E1→E4 全部落地，E5（J7-J8 P2 旅程）留白。
> 提交：f2e5fb2（E1 基建）→ aeb9c17 + 1be80d5（E2 + 文章页崩溃修复）→ b650dc5（E3）→ aa076af（E4 CI）。
> 关键词：e2e、Playwright、单进程真服务、FakeProvider 剧本、CI 第八道门。

## 1. 交付内容

- **驱动器** [tests/e2e_server.py](../../tests/e2e_server.py)：单进程真服务——
  `create_app(registry, static_dir=frontend/dist, corpus_root=tmp)` 同时装配 REST/WS
  与前端 build 产物；session factory 注入 FakeProvider 剧本（按 slug 前缀路由：
  `e2e-w*` → write 剧本，其余 → intake 剧本）；预置 `e2e-home`（空工作区）与
  `e2e-writing`（writing 阶段 + 角色 ×2 + ch01 章纲与正文）两个盘上工作区。
- **剧本件** [tests/fake_scripts.py](../../tests/fake_scripts.py)：新增 `chapter_write`
  （write_chapter force 覆盖调用，与预置正文同文保证重放幂等）。
- **前端侧**：`@playwright/test` 精确锁版进 devDependencies；
  [playwright.config.ts](../../frontend/playwright.config.ts)（端口 8931、`retries: 0`、
  `workers: 1`、webServer 自动 `npm run build && uv run --directory .. python -m
  tests.e2e_server`、`trace: retain-on-failure`）；[tsconfig.e2e.json](../../frontend/tsconfig.e2e.json)
  独立编译 e2e 目录（主 tsc include 限 `src`，build 门不依赖 playwright）；
  vitest include 本就限 `src/**.test.*`，两套 runner 天然互不收。
- **六条旅程** J1-J6（e2e/*.spec.ts）：冒烟/新建委托流式渲染/正文三态/账本搜索/
  删守门/多工作区切换，全部 `getByRole` + 中文可访问名断言，零 `waitForTimeout`。
- **CI 第八道门**（[ci.yml](../../.github/workflows/ci.yml) e2e job）：uv sync + npm ci +
  chromium 缓存（键含 lock 哈希）+ `npx playwright test`；失败上传 trace 7 天。

## 2. 关键决策（落地与偏离）

- **工作区历史状态直接落盘，agent 行为走剧本**（对方案 §5 的一处收窄）：
  write_chapter 有硬前置（writing 阶段 + 章纲 + 角色档案）。让 FakeProvider 从
  intake 一路脚本化推进到 writing 需要跨过确认门链（角色 ×2 + 手法锁定 + 章纲），
  剧本链长且脆。改由 driver 预置盘上状态（`seed_writing_workspace`），剧本只保留
  write_chapter 调用本身——阶段/账本属「作者侧事实」而非 agent 行为，L1 已覆盖
  门链逻辑。write 剧本覆盖写与预置正文同文，重放幂等。
- **reconcile_debounce 拉到 3600s**：保存后对账队列 3s 后自动发 prompt，会额外消费
  剧本流并在断言窗口内改写正文。确定性优先，对账链路已由 L1 覆盖，E2E 隔离之。
- **J2 首跑即抓到真回归**：ArticlesPage 删除按钮的 `AlertDialogTrigger` 裸露在
  `AlertDialog` 根之外，Radix 上下文检查抛错，「文章」页整页落 ErrorBoundary。
  受控弹窗（`open={confirmSlug !== null}`）本不需要 Trigger，去掉即修复（aeb9c17）。
  vitest 纯函数层测不到此类 wiring——这正是 L2 层存在的理由。
- 剧本容错不靠动态补流：FakeProvider 流耗尽即空流（harness 无事件可放），靠
  8 周期冗余兜底（单旅程最多消耗 3 流）。

## 3. Agent 设计经验（四透镜）

### 3.1 错误消息是喂给模型的 API → 服务端硬约束的拒绝理由，是喂给测试者的诊断面

J3 首跑保存失败、对账按钮死活不亮，trace 里只有 UI 表象；根因是测试把编辑框
当成纯正文——而受管写端点的 `validate_title_heading` 强制标题行。这与模型侧的
处境同构：**工具层的不变量拒绝时，三要素（哪里错/为什么错/怎样算对）是唯一线索**。
测试编写者与模型一样，只能靠契约修正行为（保留 `# 第1章` 标题行），不存在旁路。
E2E 断言因此也应「读契约文案」而非读表象——与本层 §6 的反脆弱约定互为印证。

### 3.2 硬约束放工具层，prompt 只负责解释世界 → 预置事实放盘上，剧本只演 agent

write 场景的取舍是本透镜的直接应用：**「状态从哪来」的归属决定它放哪层**。
工作区阶段/账本是作者侧事实（工具层前置），直接落盘；agent 的产出行为（write_chapter
调用）才是剧本职责。如果把所有东西都塞进剧本（让 FakeProvider 表演完整门链推进），
等于让测试数据伪装成行为——链路一长必脆。driver 与测试代码因此保持极薄。

### 3.3 泄压阀——三情形在 E2E 编排里的对应

- **违规**（测试错）：`retries: 0` 零重试政策——红即缺陷，绝不掩盖；J3/J4 首跑
  各抓到一个断言口径错误，修测试而不是调超时。
- **空结果**（合法但无数据）：空账本断言空态文案「该账本暂无条目…」，与角色账本
  的行渲染分开断——空态是契约的一部分，不是「没等到数据」。
- **环境故障**：driver 对 static_dir 缺失 fail fast 三要素（哪里错/为什么/怎样算对），
  与生产入口同口径；CI 端口探活 `/api/health` 由 webServer 托管。

### 3.4 可观测性是事件契约的一部分 → trace 断言域按层归档

失败留 `trace.zip`（retain-on-failure）+ CI 上传 7 天——E2E 的「事件流」不是打印
日志而是 trace：action 前后的 DOM 快照、网络、console 全在案。J2 的崩溃修复之所以
五分钟内定位，靠的就是 trace 里的异常栈直达 Radix 上下文检查。分层归档与后端
「事件契约服务两类读者」同理：trace 给开发者，报告（show-report）给走查复现。

## 4. 评审记录

- 方案评审：用户批准 e2e-test-plan.md 后实施（「按这个方案开始实施」）。

## 5. 踩过的坑（代码级辅助记录）

- Playwright webServer 的 `uv run --directory ..` 是 Windows/CI 同构的关键：
  命令从 frontend/ 起跑，`--directory` 先切根再 `-m tests.e2e_server`。
- Radix Select 的 `aria-label` 会覆盖内容作为 accessible name——断言选择器
  「当前值」用 `getByLabel("章节")` toContainText，不能用按钮名匹配。
- 编辑框内容是含标题行的整文件（受管写端点校验），fill 必须保留 `# 第N章`。
- 章节搜索命中的按钮名是「第N章 标题」（buildChapterDoc 的 name），不是文件 id。
- Windows 本地跑 E2E：首次 `npx playwright install chromium`；`reuseExistingServer`
  允许挂着 8931 服务迭代测试，CI（CI=true）永远新起。

## 6. 验证输出存档

- 本地全套：`npx playwright test` → **6 passed (36.7s)**，retries=0。
- 八门全绿：pytest 641 / ruff check / ruff format / mypy 78 files /
  frontend build（tsc --noEmit）/ vitest / e2e 6。
- J1-J6 单测时长均在数秒级，套件 <1 分钟（含 build 起服务 ~30s）。

## 7. 已知留白（转后续）

- **J7 事件流/审计中心、J8 断线重连**（P2）：待需时补；J8 的快照重放断言
  （loadSnapshot 教训守门）优先级最高。
- **设置页旅程**：等 ollama WIP 合入 + SettingsPanel 表单族迁移同批补。
- CI 的 e2e job 尚未在真实 runner 上跑过（本地全绿；push 后首跑需关注
  `--with-deps` 系统依赖与缓存命中）。
- actions/cache 与 upload-artifact 未 SHA pin（与 setup-node 同批待补）。

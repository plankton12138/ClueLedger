# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: story-workflow-online.spec.ts >> 创建表单实际保存节点线索并在刷新后显示
- Location: e2e\story-workflow-online.spec.ts:53:1

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByRole('region', { name: '构思结构' }).getByRole('button', { name: '发现', exact: true })
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" getByRole('region', { name: '构思结构' }).getByRole('button', { name: '发现', exact: true }) with timeout 5000ms
  - waiting for getByRole('region', { name: '构思结构' }).getByRole('button', { name: '发现', exact: true })

```

```yaml
- banner:
  - text: 线簿
  - combobox "选择作品目录":
    - option "（未打开目录）"
    - option "空白构思" [selected]
  - button "作品"
  - navigation "作品页面":
    - link "写作":
      - /url: "#/workspaces/e21f71d69c6aa664/writing"
    - link "模块库":
      - /url: "#/library?workspace=e21f71d69c6aa664"
  - button "设置"
- main:
  - button "目录" [expanded] [pressed]
  - button "构思" [pressed]
  - button "对话" [pressed]
  - button "本章线索"
  - button "活动记录"
  - region "目录":
    - navigation "作品目录":
      - textbox "搜索正文与设定":
        - /placeholder: 搜索…
      - button "查找"
      - heading "正文" [level=2]
      - button "导入"
      - list:
        - listitem: 还没有章节。对右边说「写第一章」。
      - region "作品模块":
        - heading "模块" [level=2]
        - button "添加模块":
          - img
        - paragraph: 还没有模块
  - separator "目录栏宽度"
  - region "构思结构":
    - tablist "方案":
      - tab "主方案 主" [selected]
    - button "试读"
    - button "候选正文"
    - button "建备选"
    - button "撤销上一次"
    - group "结构图":
      - button "对质 1 条线索": 对质 1
      - button "发现 1 条线索": 发现 1
    - complementary "节点详情":
      - paragraph: 点一个节点看它的安排、线索与正文入口。
  - separator "对话栏宽度"
  - tablist "写作辅助":
    - tab "对话" [selected]
    - tab "本章线索"
  - tabpanel "对话":
    - complementary:
      - combobox "选择对话":
        - option "（新对话）"
        - option "创建构思： 建立一个很小的构思：只有「发现」和「…" [selected]
      - button "新对话"
      - button "改名"
      - combobox "当前连接与模型"
      - combobox "思考深度":
        - option "思考：默认" [selected]
        - option "思考：关闭"
        - option "思考：低"
        - option "思考：高"
        - option "思考：最大"
      - button "管理"
      - log "讨论内容":
        - region "一轮对话":
          - text: 创建构思： 建立一个很小的构思：只有「发现」和「对质」两个事件节点，一条「湿手套」线索。发现时侦探在前台看见滴水的手套，对质时询问管家；两个节点以前后推进连接。真相尚未确定，保持未定。只保存这些节点、线索和连接。
          - group: 写作｜上下文 4 个分区 · 4.1k 字符
          - group: 任务卡｜完成｜没有改动
          - group: 思考 · 输出 2.1 秒
          - paragraph: 我先读取当前方案（空方案），再建立这两个节点、一条线索和一条推进连接。
          - button "复制"
          - button "展开阅读"
          - group: ✗ set_task_scope <1 毫秒 [TASK_SCOPE_EVIDENCE_INVALID] task_scope.set：修改任务至少要登记一个确切的路径、装配操作、内容保存或库发布。建议：按作者原话与确切路径重新登记；引用作者消息时保持文字一致
          - group: ✓ read_story <1 毫秒
          - group: 思考 · 输出 2.7 秒
          - group: ✗ update_story <1 毫秒 [STORY_CHANGE_INVALID] story.update:operations.0.add_clue.clue.resolution：Value error, 未解释或未定的线索不应指向回收节点。建议：按工具参数的字段名与类型重新提交；先用 read_story 确认现有 ID
          - group: 思考 · 输出 1.8 秒
          - group: ✓ update_story 62 毫秒
          - paragraph: 已保存到构思结构（方案 main，修订 rev_79d30174）：
          - paragraph:
            - strong: 节点
          - list:
            - listitem:
              - code: node-discover
              - text: ｜发现：侦探在前台看见一副正在滴水的手套，此时不知道手套何时、被谁弄湿。
            - listitem:
              - code: node-confront
              - text: ｜对质：侦探以手套的湿为话头询问管家，要他说明手套为何是湿的；管家的说法尚未展开。
          - paragraph:
            - strong: 线索
          - list:
            - listitem:
              - code: clue-wet-glove
              - text: ｜湿手套：出现在「发现」中（正往下滴水），在「对质」中被用来追问管家。作者真相留空，回收意图标为「未决」——没有指认湿的原因、时间，也没有把线索挂到任何结论上。
          - paragraph:
            - strong: 连接
          - list:
            - listitem:
              - code: edge-discover-confront
              - text: ：发现 → 对质，类型 next（前后推进）。
          - paragraph: 按你的要求只存了这些，没有建别的节点、不写正文、不动任何文件。之后若要补线索细节（比如手套是谁的、水滴落在哪里）或给管家的回答定案，直接说即可。
          - button "复制"
          - button "展开阅读"
          - text: 完成 没有改动
          - paragraph: 构思结构已提交 4 项；创作意图是否完整仍需你核对。
      - button "+ 添加文件"
      - text: 可选：让这一轮参考其他文件
      - textbox "说要求，回车发送，Shift+Enter 换行"
      - button "当前上下文占用": 上下文 12%
      - button "本次对话累计用量": 累计 1.5 万
      - button "发送" [disabled]
- button "灵感"
```

# Test source

```ts
  1   | /**
  2   |  * 构思在线完整流程（1.0.1 P6）：真实服务、真实模型、真实文件。
  3   |  *
  4   |  * 验证三件事：模型通过结构工具建立方案与线索；备选的确定采用落到主方案；
  5   |  * 「引用到对话」让下一轮请求真的带上结构注入（读操作上下文，不猜）。
  6   |  * 断言只放可判定的事实；语义质量由 eval harness 的 O01/O06 等样例单独评。
  7   |  */
  8   | import { test, expect, type APIRequestContext, type Page } from "@playwright/test";
  9   | import { mkdirSync, mkdtempSync } from "node:fs";
  10  | import { join, resolve } from "node:path";
  11  |
  12  | type Accepted = { conversation_id: string; turn_id: string; operation_id?: string };
  13  | type Event = Record<string, unknown> & { type: string };
  14  |
  15  | async function events(request: APIRequestContext, wid: string, accepted: Accepted): Promise<Event[]> {
  16  |   const response = await request.get(
  17  |     `/api/file-workspaces/${wid}/conversations/${accepted.conversation_id}/messages`,
  18  |   );
  19  |   expect(response.ok(), await response.text()).toBeTruthy();
  20  |   return await response.json().then((body) => body.events);
  21  | }
  22  |
  23  | async function finish(request: APIRequestContext, wid: string, accepted: Accepted): Promise<Event[]> {
  24  |   let rows: Event[] = [];
  25  |   await expect
  26  |     .poll(async () => {
  27  |       rows = await events(request, wid, accepted);
  28  |       return rows.some((e) => e.type === "run_finished" && e.turn_id === accepted.turn_id);
  29  |     }, { timeout: 240_000, intervals: [500, 1000] })
  30  |     .toBe(true);
  31  |   const ended = rows.find((e) => e.type === "run_finished" && e.turn_id === accepted.turn_id)!;
  32  |   expect(ended.status, JSON.stringify(rows.filter((e) => e.type === "error"))).toBe("completed");
  33  |   return rows;
  34  | }
  35  |
  36  | async function accept(page: Page, action: () => Promise<unknown>): Promise<Accepted> {
  37  |   const received = page.waitForResponse(
  38  |     (r) =>
  39  |       r.request().method() === "POST" &&
  40  |       r.status() === 202 &&
  41  |       /\/(conversations(?:\/[^/]+\/turns)?|operations)$/.test(new URL(r.url()).pathname),
  42  |     { timeout: 20_000 },
  43  |   );
  44  |   await action();
  45  |   return await (await received).json();
  46  | }
  47  |
  48  | async function send(page: Page, text: string): Promise<Accepted> {
  49  |   await page.locator(".chat-input").fill(text);
  50  |   return accept(page, () => page.getByRole("button", { name: "发送", exact: true }).click());
  51  | }
  52  |
  53  | test("创建表单实际保存节点线索并在刷新后显示", async ({ page, request }, info) => {
  54  |   test.setTimeout(300_000);
  55  |   const root = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e", "online-books");
  56  |   mkdirSync(root, { recursive: true });
  57  |   const path = join(mkdtempSync(join(root, "story-form-")), "空白构思");
  58  |   const created = await request.post("/api/file-workspaces", { data: { path, create: true } });
  59  |   expect(created.ok(), await created.text()).toBeTruthy();
  60  |   const book = await created.json();
  61  |   const base = `/api/file-workspaces/${book.id}`;
  62  |   const config = await request.get("/api/model-connections").then(r => r.json());
  63  |   expect(config.selection.available).toBe(true);
  64  |   const model = config.connections.find((item: { name: string }) => item.name === config.selection.connection_id);
  65  |   expect(["localhost", "127.0.0.1", "0.0.0.0"]).not.toContain(new URL(model.base_url).hostname);
  66  |   await page.goto(`/#/workspaces/${book.id}/writing`);
  67  |   await page.getByRole("button", { name: "构思", exact: true }).click();
  68  |   await page.getByRole("region", { name: "构思结构" }).getByRole("button", { name: "创建构思", exact: true }).click();
  69  |   const dialog = page.getByRole("dialog", { name: "创建构思", exact: true });
  70  |   await expect(dialog).toBeVisible();
  71  |   await dialog.getByLabel("创作要求").fill(
  72  |     "建立一个很小的构思：只有「发现」和「对质」两个事件节点，一条「湿手套」线索。发现时侦探在前台看见滴水的手套，对质时询问管家；两个节点以前后推进连接。真相尚未确定，保持未定。只保存这些节点、线索和连接。",
  73  |   );
  74  |   await page.screenshot({ path: info.outputPath("creation-form.png") });
  75  |   const accepted = await accept(page, () => dialog.getByRole("button", { name: "创建构思", exact: true }).click());
  76  |   await expect(dialog).not.toBeVisible();
  77  |   const rows = await finish(request, book.id, accepted);
  78  |   const turn = rows.filter(row => row.turn_id === accepted.turn_id);
  79  |   expect(turn.find(row => row.type === "task_intent")?.kind).toBe("structure");
  80  |   expect(turn.find(row => row.type === "task_intent")?.action).toBe("create");
  81  |   expect(turn.some(row => row.type === "module_changes_applied")).toBe(false);
  82  |   expect(turn.some(row => row.type === "story_changes_committed")).toBe(true);
  83  |   const graph = await request.get(`${base}/story/variants/main`).then(r => r.json());
  84  |   expect(graph.nodes).toHaveLength(2);
  85  |   expect(graph.nodes.map((node: { title: string }) => node.title)).toEqual(expect.arrayContaining(["发现", "对质"]));
  86  |   expect(graph.edges.length).toBeGreaterThanOrEqual(1);
  87  |   const variants = await request.get(`${base}/story/variants`).then(r => r.json());
  88  |   expect(variants.variants[0].counts.clues).toBe(1);
  89  |   const surface = page.getByRole("region", { name: "构思结构" });
> 90  |   await expect(surface.getByRole("button", { name: "发现", exact: true })).toBeVisible();
      |                                                                          ^ Error: expect(locator).toBeVisible() failed
  91  |   await page.reload();
  92  |   await page.getByRole("button", { name: "构思", exact: true }).click();
  93  |   await expect(surface.getByRole("button", { name: "对质", exact: true })).toBeVisible();
  94  |   await surface.getByRole("button", { name: "发现", exact: true }).click();
  95  |   await expect(surface.getByText("湿手套", { exact: false }).first()).toBeVisible();
  96  |   await page.screenshot({ path: info.outputPath("created-structure.png") });
  97  |   await info.attach("creation-evidence", { body: JSON.stringify({
  98  |     model: config.selection.model, accepted, graph,
  99  |     events: turn.filter(row => ["task_intent", "story_changes_committed", "run_finished", "error", "model_request_finished"].includes(row.type)),
  100 |   }, null, 2), contentType: "application/json" });
  101 | });
  102 |
  103 | test("构思建立、备选采用与结构引用都进入真实请求", async ({ page, request }, info) => {
  104 |   test.setTimeout(720_000);
  105 |   const root = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e", "online-books");
  106 |   mkdirSync(root, { recursive: true });
  107 |   const path = join(mkdtempSync(join(root, "story-")), "雪夜山庄");
  108 |   const created = await request.post("/api/file-workspaces", { data: { path, create: true } });
  109 |   expect(created.ok()).toBeTruthy();
  110 |   const book = await created.json();
  111 |   const base = `/api/file-workspaces/${book.id}`;
  112 |   const transcript: unknown[] = [];
  113 |   try {
  114 |     const config = await request.get("/api/model-connections").then((r) => r.json());
  115 |     expect(config.selection.available).toBe(true);
  116 |     const connection = config.connections.find(
  117 |       (c: { name: string }) => c.name === config.selection.connection_id,
  118 |     );
  119 |     // 在线评测不指向本机：接错了就停，不冒充真实模型结果。
  120 |     expect(["localhost", "127.0.0.1", "0.0.0.0"]).not.toContain(new URL(connection.base_url).hostname);
  121 |
  122 |     await page.goto(`/#/workspaces/${book.id}/writing`);
  123 |     // 打开构思面：这一轮按 design 语境发送，空白作品也能建立第一份结构。
  124 |     await page.getByRole("button", { name: "构思", exact: true }).click();
  125 |
  126 |     // 1) 从空白构思：模型用结构工具建立线索与两个节点。
  127 |     const plan = await send(
  128 |       page,
  129 |       "为这部小说建立构思：一条线索「窗台的雪」——管家声称整夜没出房间，但窗台内侧有他的脚印，作者真相是他在雪停后去过书房；再给两个节点「晚宴」和「对质」，晚宴呈现脚印的疑点，对质时管家解释脚印。只建结构与台账，不写正文。",
  130 |     );
  131 |     transcript.push(await finish(request, book.id, plan));
  132 |     const variants = await request.get(`${base}/story/variants`).then((r) => r.json());
  133 |     const main = variants.variants[0];
  134 |     expect(main.counts.nodes).toBeGreaterThanOrEqual(2);
  135 |     expect(main.counts.clues).toBeGreaterThanOrEqual(1);
  136 |     expect(variants.primary_variant_id).toBe(main.id);
  137 |
  138 |     // 2) 构思面可读；建备选后采用，主方案多出备选的节点。
  139 |     if (!(await page.getByRole("region", { name: "构思结构" }).isVisible())) {
  140 |       await page.getByRole("button", { name: "构思", exact: true }).click();
  141 |     }
  142 |     const surface = page.getByRole("region", { name: "构思结构" });
  143 |     await expect(surface.getByRole("button").filter({ hasText: /.+/ }).first()).toBeVisible();
  144 |     await surface.getByRole("button", { name: "建备选" }).click();
  145 |     await expect(surface.getByRole("tab", { name: /备选/ })).toBeVisible();
  146 |     const alt = await request
  147 |       .get(`${base}/story/variants`)
  148 |       .then((r) => r.json())
  149 |       .then(
  150 |         (body: { variants: { id: string; revision: string; primary: boolean }[] }) =>
  151 |           body.variants.find((item) => !item.primary),
  152 |       );
  153 |     const altRevision = alt.revision;
  154 |     await request.fetch(`${base}/story/changes`, {
  155 |       method: "post",
  156 |       data: {
  157 |         variant_id: alt.id,
  158 |         expected_revision: altRevision,
  159 |         request_key: `story-online-alt-${book.id}`,
  160 |         operations: [{ op: "add_node", node: { id: "n9", title: "尾声", action: "雪停了，脚印被扫掉" } }],
  161 |       },
  162 |     });
  163 |     await page.reload();
  164 |     await page.getByRole("button", { name: "构思", exact: true }).click();
  165 |     const surface2 = page.getByRole("region", { name: "构思结构" });
  166 |     await surface2.getByRole("tab", { name: /备选/ }).click();
  167 |     await surface2.getByRole("button", { name: "采用此方案" }).click();
  168 |     await expect(surface2.getByRole("region", { name: "采用结果" })).toContainText("已复制到主方案");
  169 |     const merged = await request
  170 |       .get(`${base}/story/variants/${main.id}`)
  171 |       .then((r) => r.json());
  172 |     expect(merged.nodes.some((node: { id: string }) => node.id === "n9")).toBe(true);
  173 |
  174 |     // 3) 引用到对话：下一条请求真的带上结构注入（读操作上下文，不猜）。
  175 |     await surface2.getByRole("button", { name: "尾声", exact: true }).click();
  176 |     await surface2.getByRole("button", { name: "引用到对话" }).click();
  177 |     await expect(page.getByLabel("引用构思")).toContainText("尾声");
  178 |     const discussed = await send(
  179 |       page,
  180 |       "只讨论、不保存：以「尾声」这个节点为基点，说明如果雪停时间被目击者推翻，哪些节点和线索需要联动调整。给出需要改动的对象清单即可。",
  181 |     );
  182 |     const rows = await finish(request, book.id, discussed);
  183 |     const operationId = rows.find((e) => e.type === "operation_started")?.operation_id;
  184 |     if (typeof operationId === "string" && operationId) {
  185 |       const context = await request
  186 |         .get(`/api/operations/${operationId}/context`)
  187 |         .then((r) => r.json());
  188 |       const injected = JSON.stringify(context.injections ?? []);
  189 |       // 结构真的进入了这一轮的输入；没有就说明「引用到对话」是假的。
  190 |       expect(injected).toContain("story");
```

# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: v2-workbench.spec.ts >> 正文冲突保留草稿，切换项目需明确放弃；版本恢复不删除历史
- Location: e2e\v2-workbench.spec.ts:155:1

# Error details

```
Test timeout of 30000ms exceeded.
```

```
Error: locator.fill: Test timeout of 30000ms exceeded.
Call log:
  - waiting for getByRole('textbox', { name: '恢复来源提交' })

```

# Page snapshot

```yaml
- generic [ref=f2e3]:
  - banner [ref=f2e4]:
    - generic [ref=f2e5]: H
    - generic [ref=f2e6]:
      - generic [ref=f2e7]: Hastings
      - generic [ref=f2e8]: STORY WORKSPACE
    - combobox "选择项目" [ref=f2e10] [cursor=pointer]:
      - generic: 冲突保护
    - textbox "全局搜索" [ref=f2e18]:
      - /placeholder: 搜索章节、角色与线索
    - status [ref=f2e19]: 已连接
    - generic [ref=f2e21]: 作
    - button "切换模型" [ref=f2e22] [cursor=pointer]: qwen3:4b ▾
    - button "隐藏对话栏" [pressed] [ref=f2e23] [cursor=pointer]:
      - generic [ref=f2e26]: 对话
    - button "设置" [ref=f2e27] [cursor=pointer]
  - navigation "主导航" [ref=f2e31]:
    - button "项目概览" [ref=f2e32] [cursor=pointer]
    - button "创作台" [ref=f2e38] [cursor=pointer]
    - button "故事账本" [ref=f2e42] [cursor=pointer]
    - button "变更影响" [ref=f2e45] [cursor=pointer]
    - button "修订审阅" [ref=f2e50] [cursor=pointer]
    - button "悬疑审计" [ref=f2e54] [cursor=pointer]
    - button "作者模型" [ref=f2e59] [cursor=pointer]
  - generic [ref=f2e63]:
    - main "创作台" [ref=f2e65]:
      - complementary [ref=f2e66]:
        - generic [ref=f2e67]:
          - generic [ref=f2e68]:
            - heading "协作主编" [level=2] [ref=f2e69]
            - generic [ref=f2e70]: 候选先审阅，再进入正式稿
          - generic [ref=f2e71]:
            - generic [ref=f2e72]:
              - text: 协作任务
              - combobox "协作任务" [ref=f2e73]:
                - option "规划故事与节拍" [selected]
                - option "生成正文候选"
                - option "检查故事一致性"
                - option "咨询协作主编"
            - generic [ref=f2e74]:
              - text: 创作要求
              - textbox "创作要求" [ref=f2e75]:
                - /placeholder: 描述故事方向、章节目的，或需要检查的问题…
            - button "提交协作任务" [disabled] [ref=f2e76]
          - article "风格观察任务" [ref=f2e78]:
            - generic [ref=f2e79]:
              - generic [ref=f2e80]: 风格观察任务
              - generic [ref=f2e81]: 任务完成
            - list [ref=f2e83]:
              - listitem [ref=f2e84]:
                - text: 措辞与句式：作者倾向精简修饰语
                - generic [ref=f2e85]: 置信度 95%
              - listitem [ref=f2e86]:
                - text: 措辞与句式：作者倾向精简修饰语
                - generic [ref=f2e87]: 置信度 95%
            - button "查看任务过程" [ref=f2e89] [cursor=pointer]
        - generic [ref=f2e90]:
          - generic [ref=f2e91]:
            - heading "章节目录" [level=2] [ref=f2e92]
            - generic [ref=f2e93]: 1 章
          - button "第一章 雨夜" [ref=f2e94] [cursor=pointer]
          - button "＋ 导入 / 新建章节" [ref=f2e95] [cursor=pointer]
        - generic [ref=f2e96]:
          - button "场景事实与叙事计划 当前项目" [ref=f2e99] [cursor=pointer]:
            - generic [ref=f2e100]:
              - heading "场景事实与叙事计划" [level=2] [ref=f2e101]
              - generic [ref=f2e102]: 当前项目
            - generic [aria-hidden] [ref=f2e103]: ▾
          - button [ref=f2e106] [cursor=pointer]:
            - heading "生效的作者偏好" [level=2] [ref=f2e108]
            - generic [aria-hidden] [ref=f2e109]: ▾
          - button [ref=f2e112] [cursor=pointer]:
            - heading "从历史版本恢复" [level=2] [ref=f2e114]
            - generic [aria-hidden] [ref=f2e115]: ▾
          - generic [ref=f2e116]: 人工保存立即生效。任务完成表示候选已返回，接受后的版本才是正式稿。
      - generic [ref=f2e117]:
        - generic [ref=f2e118]:
          - heading "第一章 雨夜" [level=1] [ref=f2e119]
          - generic [ref=f2e120]:
            - generic [ref=f2e121]: 已保存
            - button "保存正文" [disabled] [ref=f2e122]
        - textbox "人工正文" [ref=f2e125]: 另一窗口的稿子
        - generic [ref=f2e126]:
          - generic [ref=f2e127]: 7 字 · 正文优先
          - button "重新读取当前章" [ref=f2e128] [cursor=pointer]
    - complementary "对话栏" [ref=f2e129]:
      - separator "调整对话栏宽度" [ref=f2e130]
      - generic [ref=f2e131]:
        - generic [ref=f2e132]:
          - button "新对话 ▾" [ref=f2e134] [cursor=pointer]:
            - generic [ref=f2e135]: 新对话
            - generic [ref=f2e136]: ▾
          - paragraph [ref=f2e137]: 项目内对话
        - button "新建会话" [ref=f2e138] [cursor=pointer]
        - button "收起对话栏" [ref=f2e141] [cursor=pointer]
      - generic "对话消息" [ref=f2e145]:
        - paragraph [ref=f2e146]: 还没有消息。普通发送只进行对话；明确点击创作操作才会创建任务。
      - generic [ref=f2e147]:
        - generic [ref=f2e148]:
          - textbox "对话输入" [ref=f2e149]:
            - /placeholder: 讨论构想；Enter 发送，Shift+Enter 换行
          - button "发送" [disabled] [ref=f2e151]
        - generic [ref=f2e155]:
          - generic "发送前显示服务端当前选择；切换影响后续发送" [ref=f2e156]: 模型：qwen3:4b
          - button "＋ 引用" [ref=f2e158] [cursor=pointer]
          - button "✎ 创作操作" [ref=f2e159] [cursor=pointer]
  - contentinfo [ref=f2e160]:
    - generic [ref=f2e161]:
      - generic [ref=f2e162]: ● 冲突保护 · 主分支
      - generic [ref=f2e163]: 版本 cmt_e330
      - generic [ref=f2e164]: 章基础 7c90f250
      - generic [ref=f2e165]: 上次保存 —
    - generic [ref=f2e166]: ✓ 无未保存修改
    - generic [ref=f2e167]: 对话：已连接
```

# Test source

```ts
  120 |   await writer.getByRole("button", { name: "接受候选并提交" }).click();
  121 |   await expect(page.getByRole("textbox", { name: "人工正文" })).toBeEnabled();
  122 |   const edit = page.getByRole("textbox", { name: "人工正文" });
  123 |   await edit.fill("林岚递出怀表。\n\n他仍然信任这位协助者。");
  124 |   await page.getByRole("button", { name: "保存正文", exact: true }).click();
  125 |   await expect(
  126 |     page.getByRole("button", { name: "保存正文", exact: true }),
  127 |   ).toBeDisabled();
  128 |   await page.screenshot({
  129 |     path: info.outputPath("02-studio.png"),
  130 |     fullPage: true,
  131 |   });
  132 |   await page.getByRole("button", { name: "故事账本", exact: true }).click();
  133 |   await expect(page.getByText("林岚", { exact: true }).first()).toBeVisible();
  134 |   await page.getByRole("button", { name: "三层事实" }).click();
  135 |   await page.getByRole("button", { name: "角色认知", exact: true }).click();
  136 |   await page.screenshot({
  137 |     path: info.outputPath("03-ledger.png"),
  138 |     fullPage: true,
  139 |   });
  140 |   await page.getByRole("button", { name: "作者模型", exact: true }).click();
  141 |   await page
  142 |     .getByRole("textbox", { name: "写作偏好声明" })
  143 |     .fill("少用解释性独白，用动作表达情绪。");
  144 |   await page.getByRole("button", { name: "声明并生效" }).click();
  145 |   await page.getByRole("button", { name: /少用解释性独白/ }).click();
  146 |   await page.getByRole("textbox", { name: "撤销偏好原因" }).fill("本章不适用");
  147 |   await page.getByRole("button", { name: "撤销这条偏好" }).click();
  148 |   await expect(page.getByText("已撤销", { exact: true }).last()).toBeVisible();
  149 |   await page.screenshot({
  150 |     path: info.outputPath("07-author.png"),
  151 |     fullPage: true,
  152 |   });
  153 | });
  154 | 
  155 | test("正文冲突保留草稿，切换项目需明确放弃；版本恢复不删除历史", async ({
  156 |   page,
  157 |   request,
  158 | }) => {
  159 |   const { pid, imported } = await registered(page, request, "冲突保护");
  160 |   const another = await (
  161 |     await request.post("/api/v2/projects", { data: { name: "另一部作品" } })
  162 |   ).json();
  163 |   await page.evaluate((p) => {
  164 |     const registry = JSON.parse(localStorage.getItem("hastings.v2.projects")!);
  165 |     localStorage.setItem(
  166 |       "hastings.v2.projects",
  167 |       JSON.stringify([
  168 |         ...registry,
  169 |         { projectId: p.project_id, name: p.name, source: "created" },
  170 |       ]),
  171 |     );
  172 |   }, another);
  173 |   await page.reload();
  174 |   await page.getByRole("button", { name: "创作台", exact: true }).click();
  175 |   const editor = page.getByRole("textbox", { name: "人工正文" });
  176 |   await expect(editor).toHaveValue(/林岚/);
  177 |   await editor.fill("切换前的未保存草稿");
  178 |   await page.getByRole("combobox", { name: "选择项目" }).click();
  179 |   await page.getByRole("option", { name: "另一部作品" }).click();
  180 |   await expect(page.getByRole("alertdialog")).toBeVisible();
  181 |   await page.getByRole("button", { name: "返回编辑" }).click();
  182 |   await expect(editor).toHaveValue("切换前的未保存草稿");
  183 |   await page.getByRole("combobox", { name: "选择项目" }).click();
  184 |   await page.getByRole("option", { name: "另一部作品" }).click();
  185 |   await page.getByRole("button", { name: "放弃修改并切换" }).click();
  186 |   await expect(page.getByRole("textbox", { name: "人工正文" })).toHaveCount(0);
  187 |   await page.getByRole("combobox", { name: "选择项目" }).click();
  188 |   await page.getByRole("option", { name: "冲突保护" }).click();
  189 |   await expect(editor).toHaveValue(/林岚/);
  190 |   const ms = await (
  191 |     await request.get(`/api/v2/projects/${pid}/manuscript`)
  192 |   ).json();
  193 |   const chapter = ms.chapters[0];
  194 |   await editor.fill("我的未保存草稿");
  195 |   const saved = await request.post(
  196 |     `/api/v2/projects/${pid}/chapters/${chapter.chapter_id}/save`,
  197 |     {
  198 |       data: {
  199 |         idempotency_key: "external",
  200 |         full_text: "另一窗口的稿子",
  201 |         expected_chapter_base: chapter.chapter_base,
  202 |       },
  203 |     },
  204 |   );
  205 |   expect(saved.ok()).toBeTruthy();
  206 |   await page.getByRole("button", { name: "保存正文", exact: true }).click();
  207 |   await expect(
  208 |     page.getByRole("alert").filter({ hasText: /过期|冲突|变化/ }),
  209 |   ).toBeVisible();
  210 |   await expect(editor).toHaveValue("我的未保存草稿");
  211 |   await page.getByRole("button", { name: "重新读取当前章" }).click();
  212 |   await expect(page.getByRole("alertdialog")).toBeVisible();
  213 |   await page.getByRole("button", { name: "返回编辑" }).click();
  214 |   await expect(editor).toHaveValue("我的未保存草稿");
  215 |   await page.getByRole("button", { name: "重新读取当前章" }).click();
  216 |   await page.getByRole("button", { name: "放弃修改并切换" }).click();
  217 |   await expect(editor).toHaveValue("另一窗口的稿子");
  218 |   await page
  219 |     .getByRole("textbox", { name: "恢复来源提交" })
> 220 |     .fill(imported.commit_id);
      |      ^ Error: locator.fill: Test timeout of 30000ms exceeded.
  221 |   await page.getByRole("button", { name: "恢复为当前正文" }).click();
  222 |   await expect(editor).toHaveValue(/林岚/);
  223 | });
  224 | 
  225 | for (const accept of [false, true])
  226 |   test(`变更影响 → 批准 → 分支修订 → ${accept ? "接受" : "拒绝"}补丁 → 审计与导出`, async ({
  227 |     page,
  228 |     request,
  229 |   }, info) => {
  230 |     const { pid } = await registered(page, request, "局部修订");
  231 |     const ms = await (
  232 |       await request.get(`/api/v2/projects/${pid}/manuscript`)
  233 |     ).json();
  234 |     const unit = ms.chapters[0].units[0].unit_id as string;
  235 |     const opened = await request.post(`/api/v2/projects/${pid}/changes`, {
  236 |       data: {
  237 |         idempotency_key: "change",
  238 |         intent: "协助者动机变化",
  239 |         base_commit_id: ms.commit_id,
  240 |         seed_ids: [unit],
  241 |         seed_patches: {
  242 |           [unit]: {
  243 |             schema_version: 1,
  244 |             unit_id: unit,
  245 |             chapter_id: ms.chapters[0].chapter_id,
  246 |             text: "他开始怀疑协助者。",
  247 |             state: "active",
  248 |             origin: "import",
  249 |             scene_id: null,
  250 |             derived_from: [],
  251 |           },
  252 |         },
  253 |       },
  254 |     });
  255 |     expect(opened.ok()).toBeTruthy();
  256 |     const cs = (await opened.json()).change_set_id;
  257 |     await page.reload();
  258 |     await page.getByRole("button", { name: "变更影响", exact: true }).click();
  259 |     await page.getByRole("button", { name: "分析影响", exact: true }).click();
  260 |     await page.getByRole("button", { name: "制定修订计划" }).click();
  261 |     await page.screenshot({
  262 |       path: info.outputPath("04-impact.png"),
  263 |       fullPage: true,
  264 |     });
  265 |     await page.getByRole("button", { name: "批准所示范围与计划" }).click();
  266 |     await page.getByRole("button", { name: "在分支执行修订" }).click();
  267 |     await page.getByRole("button", { name: "生成审阅差异" }).click();
  268 |     await page.getByRole("button", { name: "进入修订审阅" }).click();
  269 |     await expect(page.getByRole("checkbox")).toHaveCount(1);
  270 |     if (accept) await page.getByRole("checkbox").check();
  271 |     await page.screenshot({
  272 |       path: info.outputPath("05-review.png"),
  273 |       fullPage: true,
  274 |     });
  275 |     await page.getByRole("button", { name: "提交所选差异" }).click();
  276 |     await expect(
  277 |       page.getByRole("status").filter({ hasText: /所选差异已提交/ }),
  278 |     ).toBeVisible();
  279 |     const detail = await (
  280 |       await request.get(`/api/v2/projects/${pid}/changes/${cs}`)
  281 |     ).json();
  282 |     expect(detail.change_set.status).toBe("committed");
  283 |     const saved = await (
  284 |       await request.get(`/api/v2/projects/${pid}/manuscript`)
  285 |     ).json();
  286 |     expect(saved.chapters[0].units[0].text).toBe(
  287 |       accept ? "他开始怀疑协助者。" : "林岚递出怀表。",
  288 |     );
  289 |     await page.getByRole("button", { name: "悬疑审计", exact: true }).click();
  290 |     const auditing = page.waitForResponse(
  291 |       (r) => r.url().endsWith("/audit") && r.request().method() === "POST",
  292 |     );
  293 |     await page.getByRole("button", { name: "运行一致性审计" }).click();
  294 |     expect((await auditing).ok()).toBeTruthy();
  295 |     await expect(
  296 |       page.getByRole("button", { name: "运行一致性审计" }),
  297 |     ).toBeEnabled();
  298 |     await page.screenshot({
  299 |       path: info.outputPath("06-audit.png"),
  300 |       fullPage: true,
  301 |     });
  302 |     const zip = await request.get(`/api/v2/projects/${pid}/export`);
  303 |     expect(zip.ok()).toBeTruthy();
  304 |     expect(zip.headers()["content-type"]).toContain("zip");
  305 |   });
  306 | 
  307 | test("设置只管理原生项目，不读取旧工作区", async ({ page }) => {
  308 |   const legacyRequests: string[] = [];
  309 |   page.on("request", (r) => {
  310 |     if (r.url().includes("/v2-migration/") || r.url().endsWith("/api/workspaces"))
  311 |       legacyRequests.push(r.url());
  312 |   });
  313 |   await page.goto("/");
  314 |   await page.getByRole("button", { name: "设置", exact: true }).click();
  315 |   await expect(page.getByRole("textbox", { name: "新故事名称" })).toBeVisible();
  316 |   await expect(page.getByRole("textbox", { name: "已有项目编号" })).toBeVisible();
  317 |   await expect(page.getByText("旧工作区迁移", { exact: true })).toHaveCount(0);
  318 |   await expect(page.getByRole("combobox", { name: "待迁移工作区" })).toHaveCount(0);
  319 |   expect(legacyRequests).toEqual([]);
  320 | });
```
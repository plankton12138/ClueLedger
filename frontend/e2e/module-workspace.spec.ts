import { test, expect, type APIRequestContext } from "@playwright/test";
import { mkdirSync, mkdtempSync } from "node:fs";
import { resolve, join } from "node:path";

async function fixture(request: APIRequestContext) {
  const parent = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e", "books");
  mkdirSync(parent, { recursive: true });
  const root = mkdtempSync(join(parent, "modules-"));
  async function post(path: string, data: unknown) {
    const response = await request.post(path, { data });
    expect(response.ok(), await response.text()).toBeTruthy();
    return response.json();
  }
  const book = await post("/api/file-workspaces", { path: join(root, "雾港来信"), create: true });
  const other = await post("/api/file-workspaces", { path: join(root, "另一部作品"), create: true });
  const created = await post("/api/module-library/modules", {
    kind: "reference", name: `雾港疑案-${book.id}`, request_key: crypto.randomUUID(),
    entries: [{ entry_id: "content", title: `雾港疑案-${book.id}`, body: "## 角色小传\n\n陈默修理码头的钟。\n\n## 线索\n\n钟慢了七分钟。\n\n## 真相\n\n陈默没有登船。" }],
  });
  const detail = await request.get(`/api/module-library/modules/${created.module.module_id}/revisions/${created.module.head}`).then(response => response.json());
  const ref = { library_id: detail.library_id, module_id: created.module.module_id, revision_id: created.module.head };
  return { book, other, created, ref };
}

test("添加、侧栏目录、作品内保存与其他作品隔离，刷新和移除撤销", async ({ page, request }) => {
  const { book,other,created,ref } = await fixture(request);
  await request.put(`/api/file-workspaces/${other.id}/assembly`, { data: { bindings: [{ module: ref }], expected_digest: null, request_key: crypto.randomUUID() } });
  await page.goto(`/#/workspaces/${book.id}/writing`);
  const directory = page.getByRole("navigation", { name: "作品目录", exact: true });
  await expect(directory.getByText("还没有模块", { exact: true })).toBeVisible();
  await expect(directory.getByText("角色小传", { exact: true })).toHaveCount(0);
  await expect(directory.getByText("真相", { exact: true })).toHaveCount(0);
  await directory.getByRole("button", { name: "添加模块", exact: true }).click();
  const library = page.getByRole("region", { name: "模块库", exact: true });
  await library.getByLabel("搜索模块").fill(book.id);
  await library.getByLabel("搜索模块").press("Enter");
  const card = library.locator(".module-library-card").filter({ hasText: created.module.name });
  await card.getByRole("button", { name: "＋ 添加", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/workspaces/${book.id}/writing`));
  await directory.getByRole("button", { name: "真相", exact: true }).click();
  const paper = page.getByRole("article", { name: "模块内容", exact: true });
  await expect(paper).toContainText("陈默没有登船。");
  await paper.getByRole("button", { name: "编辑", exact: true }).click();
  await paper.getByLabel("模块名称").fill("雾港疑案·本作品");
  await paper.getByLabel("模块正文", { exact: true }).fill("## 角色小传\n\n陈默改成了渡轮检修员。\n\n## 新的线索\n\n船票被撕掉了一角。");
  await paper.getByRole("button", { name: "保存", exact: true }).click();
  await expect(paper.getByText("已保存到当前作品。", { exact: true })).toBeVisible();
  await expect(directory.getByRole("button", { name: "新的线索", exact: true })).toBeVisible();
  const listing = await request.get(`/api/module-library/modules?q=${encodeURIComponent(created.module.name)}`).then(response => response.json());
  expect(listing.items.find((item: { module_id: string }) => item.module_id === ref.module_id).head).toBe(ref.revision_id);
  const otherAssembly = await request.get(`/api/file-workspaces/${other.id}/assembly`).then(response => response.json());
  expect(otherAssembly.bindings[0].module).toEqual(ref);
  await page.reload();
  await directory.getByRole("button", { name: "雾港疑案·本作品", exact: true }).click();
  await expect(paper).toContainText("船票被撕掉了一角。");
  const screenshots=resolve("../data/test-tmp/module-experience-screenshots");
  mkdirSync(screenshots,{recursive:true});
  await page.screenshot({path:join(screenshots,"writing-module-1440.png")});
  await directory.getByRole("button", { name: "管理雾港疑案·本作品", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "从作品移除", exact: true }).click();
  await expect(directory.getByRole("button", { name: "雾港疑案·本作品", exact: true })).toHaveCount(0);
  await directory.getByRole("button", { name: "撤销", exact: true }).click();
  await directory.getByRole("button", { name: "雾港疑案·本作品", exact: true }).click();
  await expect(paper).toContainText("船票被撕掉了一角。");
});

test("新建只写名称与正文，小标题生成目录且不出现类型表单", async ({ page,request }) => {
  const { book } = await fixture(request);
  await page.goto(`/#/library?workspace=${book.id}`);
  const library = page.getByRole("region", { name: "模块库", exact: true });
  await library.getByRole("button", { name: "新建模块", exact: true }).click();
  await expect(library.getByLabel("类型", { exact: true })).toHaveCount(0);
  await expect(library.getByLabel("第一条的标题", { exact: true })).toHaveCount(0);
  await library.getByLabel("模块正文", { exact: true }).fill("## 雨季规则\n\n连续下雨时提前停航。\n\n## 人物\n\n渡轮检修员知道这件事。");
  await library.getByRole("button", { name: "保存并添加", exact: true }).click();
  const directory = page.getByRole("navigation", { name: "作品目录", exact: true });
  await expect(directory.locator(".mounted-module-name").filter({hasText:"雨季规则"})).toBeVisible();
  await expect(directory.getByRole("button", { name: "人物", exact: true })).toBeVisible();
  await expect(page.getByRole("article", { name: "模块内容", exact: true })).toContainText("连续下雨时提前停航。");
});

test("作品内容保存响应丢失后重试不重复创建版本", async ({ page,request }) => {
  const { book,ref,created } = await fixture(request);
  await request.put(`/api/file-workspaces/${book.id}/assembly`, { data: { bindings: [{ module: ref }], expected_digest: null, request_key: crypto.randomUUID() } });
  const inputs: unknown[] = [];
  await page.route(`**/api/file-workspaces/${book.id}/assembly/*/content`, async route => {
    inputs.push(route.request().postDataJSON());
    if (inputs.length === 1) { await route.fetch(); await route.abort("failed"); }
    else await route.continue();
  });
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button", { name: created.module.name, exact: true }).click();
  const paper = page.getByRole("article", { name: "模块内容", exact: true });
  await paper.getByRole("button", { name: "编辑", exact: true }).click();
  await paper.getByLabel("模块正文", { exact: true }).fill("保存响应丢失时保留草稿。");
  await paper.getByRole("button", { name: "保存", exact: true }).click();
  await expect(paper.getByRole("alert")).toBeVisible();
  await expect(paper.getByLabel("模块正文", { exact: true })).toHaveValue("保存响应丢失时保留草稿。");
  await paper.getByRole("button", { name: "保存", exact: true }).click();
  await expect(paper).toContainText("已保存到当前作品。");
  expect(inputs).toHaveLength(2);
  expect(inputs[0]).toEqual(inputs[1]);
  const revisions = await request.get(`/api/module-library/modules/${ref.module_id}/revisions`).then(response => response.json());
  expect(revisions.total).toBe(2);
});

test("外部版本变化时保留草稿，重新读取后可以另存而不覆盖当前内容", async ({ page,request }) => {
  const { book,ref,created }=await fixture(request);
  const bound=await request.put(`/api/file-workspaces/${book.id}/assembly`,{data:{bindings:[{module:ref}],expected_digest:null,request_key:crypto.randomUUID()}}).then(r=>r.json());
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.getByRole("button",{name:created.module.name,exact:true}).click();
  const paper=page.getByRole("article",{name:"模块内容",exact:true});
  await paper.getByRole("button",{name:"编辑",exact:true}).click();
  await paper.getByLabel("模块正文",{exact:true}).fill("这份未保存草稿需要保留。");
  const newer=await request.post(`/api/module-library/modules/${ref.module_id}/revisions`,{data:{base_revision:ref.revision_id,expected_head:ref.revision_id,request_key:crypto.randomUUID(),entries:[{entry_id:"content",title:created.module.name,body:"外部已保存的新内容。"}]}}).then(r=>r.json());
  const currentRef={...ref,revision_id:newer.revision_id};
  await request.put(`/api/file-workspaces/${book.id}/assembly`,{data:{bindings:[{id:bound.bindings[0].id,module:currentRef}],expected_digest:bound.digest,request_key:crypto.randomUUID()}});
  await paper.getByRole("button",{name:"保存",exact:true}).click();
  await expect(paper.getByRole("alert")).toContainText("ASSEMBLY_STALE_CONFLICT");
  await paper.getByRole("button",{name:"重新读取",exact:true}).click();
  await expect(paper).toContainText("外部已保存的新内容。");
  await paper.getByRole("button",{name:"继续编辑",exact:true}).click();
  await expect(paper.getByLabel("模块正文",{exact:true})).toHaveValue("这份未保存草稿需要保留。");
  await paper.getByRole("button",{name:"另存草稿",exact:true}).click();
  await expect(paper).toContainText("已另存一份到模块库。");
  const current=await request.get(`/api/file-workspaces/${book.id}/assembly`).then(r=>r.json());
  expect(current.bindings[0].module).toEqual(currentRef);
});

test("模块库窄窗口和大字号下可浏览、添加和编辑", async ({ page,request }) => {
  test.setTimeout(60_000);
  const { book,created } = await fixture(request);
  const output = resolve("../data/test-tmp/module-experience-screenshots");
  mkdirSync(output,{recursive:true});
  await page.goto(`/#/library?workspace=${book.id}`);
  for (const width of [1440,1024,640,390]) for (const scale of [100,150]) {
    await page.setViewportSize({ width,height:900 });
    const settings = page.getByRole("button",{name:"设置",exact:true});
    await settings.click();
    await page.getByRole("dialog",{name:"设置",exact:true}).getByLabel("调整字体大小").selectOption(String(scale));
    await page.getByRole("dialog",{name:"设置",exact:true}).getByRole("button",{name:"关闭",exact:true}).click();
    const library = page.getByRole("region",{name:"模块库",exact:true});
    await library.getByLabel("搜索模块").fill(book.id);await library.getByLabel("搜索模块").press("Enter");
    await expect(library.getByRole("button",{name:created.module.name,exact:true})).toBeVisible();
    await expect(library.locator(".module-library-card").filter({hasText:created.module.name}).getByRole("button",{name:"＋ 添加",exact:true})).toBeInViewport();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:join(output,`library-${width}-${scale}.png`)});
    await library.getByRole("button",{name:created.module.name,exact:true}).click();
    await library.getByRole("button",{name:"编辑",exact:true}).click();
    await library.getByLabel("模块正文",{exact:true}).fill(`窗口 ${width}，字号 ${scale} 的修改。`);
    await expect(library.getByRole("button",{name:"保存",exact:true})).toBeEnabled();
    await expect(library.getByRole("button",{name:"保存",exact:true})).toBeInViewport();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await library.getByRole("button",{name:"取消",exact:true}).click();
    await library.getByRole("button",{name:"返回列表",exact:true}).click();
  }
});

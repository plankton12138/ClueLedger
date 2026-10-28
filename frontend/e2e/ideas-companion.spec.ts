import { test, expect } from "./fixtures/frontendExperience";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import type { Locator, Page } from "@playwright/test";

async function openIdeas(page: Page, workspaceId: string) {
  await page.goto(`/#/workspaces/${workspaceId}/writing`);
  await page.getByRole("button", {name:"灵感",exact:true}).click();
  await expect(page.locator(".idea-note").first()).toBeVisible();
  return page.getByRole("complementary", {name:"灵感",exact:true});
}
async function drag(page: Page, handle: Locator, dx: number, dy: number) {
  const box=await handle.boundingBox();
  await page.mouse.move(box!.x+20,box!.y+16); await page.mouse.down();
  await page.mouse.move(box!.x+20+dx,box!.y+16+dy,{steps:12}); await page.mouse.up();
}
async function shot(page: Page, name: string) {
  const output=resolve(process.env.HASTINGS_E2E_SCREENSHOT_DIR ?? "../data/test-tmp/ideas-companion-screenshots");
  mkdirSync(output,{recursive:true}); await page.screenshot({path:resolve(output,`${name}.png`)});
}

test("独立入口：按钮和面板可拖动，位置跨刷新保留，拖动不误打开", async({page,book})=>{
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await expect(page.getByRole("navigation",{name:"作品页面"}).getByRole("button",{name:"灵感",exact:true})).toHaveCount(0);
  const launcher=page.getByRole("button",{name:"灵感",exact:true});
  await expect(launcher).toBeVisible();
  const before=await launcher.boundingBox();
  await drag(page,launcher,-260,-80);
  const moved=await launcher.boundingBox();
  expect(moved!.x).toBeLessThan(before!.x-180);
  await expect(page.locator(".idea-surface")).toHaveCount(0);
  const position=await page.evaluate(()=>localStorage.getItem("hastings.ideas.position"));
  await page.reload();
  await expect(launcher).toBeVisible();
  expect(await page.evaluate(()=>localStorage.getItem("hastings.ideas.position"))).toBe(position);
  const restored=await launcher.boundingBox();
  expect(Math.abs(restored!.x-moved!.x)).toBeLessThan(2);
  await launcher.click();
  const panel=page.locator(".idea-floating");
  const handle=page.getByRole("button",{name:"移动灵感面板",exact:true});
  const panelBefore=await panel.boundingBox();
  await drag(page,handle,-200,60);
  const panelAfter=await panel.boundingBox();
  expect(panelAfter!.x).toBeLessThan(panelBefore!.x-120);
  await page.getByLabel("记录灵感",{exact:true}).fill("收起也保留的输入");
  await page.getByRole("link",{name:"模块库",exact:true}).click();
  await expect(page.getByLabel("记录灵感",{exact:true})).toHaveValue("收起也保留的输入");
  await page.getByRole("button",{name:"收起灵感",exact:true}).click();
  await expect(launcher).toBeFocused();
  await launcher.press("ArrowLeft");
  await launcher.press("Enter");
  await expect(page.getByLabel("记录灵感",{exact:true})).toHaveValue("收起也保留的输入");
  // 面板可越过顶栏，只保留标题栏可抓取，不再被顶栏高度限制。
  const up=Math.min(600,(await handle.boundingBox())!.y+12);
  await drag(page,handle,0,-up);
  expect((await panel.boundingBox())!.y).toBeLessThanOrEqual(12);
  await shot(page,"floating-dragged");
});

test("列表直接连续加入，保留对话草稿，长内容读取全文且不发送",async({page,request,book})=>{
  const base=`/api/file-workspaces/${book.id}/ideas`;
  const body="长灵感的开头。"+"雨落在旧港的屋顶，没有人抬头。".repeat(500);
  const long=await request.post(base,{data:{body,request_key:`long-ref-${book.id}`}}).then(r=>r.json());
  let starts=0;
  page.on("request",request=>{if(request.method()==="POST"&&/\/turns|\/material-tasks|\/conversations$/.test(request.url()))starts++;});
  await page.goto(`/#/workspaces/${book.id}/writing`);
  await page.locator(".chat-input").fill("保留原有要求");
  await page.getByRole("button",{name:"灵感",exact:true}).click();
  const note=page.locator(`[data-idea-id="${long.idea_id}"]`);
  await note.getByRole("button",{name:"加入对话",exact:true}).click();
  await expect(note.getByRole("button",{name:"✓ 已加入",exact:true})).toBeDisabled();
  await expect(page.getByLabel("灵感正文",{exact:true})).toHaveCount(0);
  await page.locator(".idea-note").filter({hasText:"侦探认出了雨衣"}).getByRole("button",{name:"加入对话",exact:true}).click();
  await expect(page.locator('.writing-chat button[aria-label^="查看引用的灵感"]')).toHaveCount(2);
  await expect(page.locator(".chat-input")).toHaveValue("保留原有要求");
  await page.getByLabel("记录灵感",{exact:true}).fill("她一直在等这封信。");
  await page.getByRole("button",{name:"记下并加入对话",exact:true}).click();
  await expect(page.locator('.writing-chat button[aria-label^="查看引用的灵感"]')).toHaveCount(3);
  await expect(page.getByLabel("记录灵感",{exact:true})).toHaveValue("");
  await expect(page.locator(".idea-surface")).toBeVisible();
  await page.getByRole("button",{name:"收起灵感",exact:true}).click();
  await page.getByRole("button",{name:/^查看引用的灵感：长灵感/}).click();
  await expect(page.getByRole("dialog",{name:"引用的灵感",exact:true}).locator("pre")).toHaveText(body);
  expect(starts).toBe(0);
});

test("原处修改保留草稿，保存后回到短内容，原有引用保持固定版本",async({page,request,book})=>{
  await openIdeas(page,book.id);
  const note=page.locator(".idea-note").first();
  await note.getByRole("button",{name:"加入对话",exact:true}).click();
  await note.locator(".idea-note-text").click();
  await page.getByLabel("灵感正文",{exact:true}).fill("侦探认出了雨衣，也认出了来客。");
  await page.getByRole("button",{name:"收起修改",exact:true}).click();
  await expect(note).toContainText("未保存");
  await note.locator(".idea-note-text").click();
  await expect(page.getByLabel("灵感正文",{exact:true})).toHaveValue("侦探认出了雨衣，也认出了来客。");
  await page.getByRole("button",{name:"完成",exact:true}).click();
  await expect(page.getByLabel("灵感正文",{exact:true})).toHaveCount(0);
  await expect(note).toContainText("侦探认出了雨衣，也认出了来客。");
  await expect(note.getByRole("button",{name:"更新引用",exact:true})).toBeEnabled();
  const oldChip=page.getByRole("button",{name:"查看引用的灵感：侦探认出了雨衣。",exact:true});
  await expect(oldChip).toBeVisible();
  const records=await request.get(`/api/file-workspaces/${book.id}/ideas`).then(r=>r.json());
  expect(records.items[0].excerpt).toBe("侦探认出了雨衣，也认出了来客。");
  await note.getByRole("button",{name:"更新引用",exact:true}).click();
  await expect(page.getByRole("button",{name:"查看引用的灵感：侦探认出了雨衣，也认出了来客。",exact:true})).toBeVisible();
});

for (const action of ["记下", "记下并加入对话"]) {
  test(`${action}后新灵感可以立即修改，不需要先切换旧条目`, async ({ page, request, book }) => {
    await openIdeas(page, book.id);
    const base = `/api/file-workspaces/${book.id}/ideas`;
    const original = `刚记录的内容：${action}`;
    await page.getByLabel("记录灵感", { exact: true }).fill(original);
    await page.getByRole("button", { name: action, exact: true }).click();
    const note = page.locator(".idea-note").filter({ has: page.locator(".idea-note-text", { hasText: original }) });
    await expect(note).toBeVisible();
    const ideaId = await note.getAttribute("data-idea-id");
    await note.locator(".idea-note-text").click();
    const body = page.getByLabel("灵感正文", { exact: true });
    await expect(body).toHaveValue(original);
    const changed = `${original}，现在补充日期。`;
    await body.fill(changed);
    await expect(body).toHaveValue(changed);
    await expect(page.getByRole("button", { name: "完成", exact: true })).toBeEnabled();
    await page.getByRole("button", { name: "完成", exact: true }).click();
    await expect(page.getByLabel("灵感正文", { exact: true })).toHaveCount(0);
    const stored = await request.get(`${base}/${ideaId}`).then(response => response.json());
    expect(stored.body).toBe(changed);
    expect(stored.idea_id).toBe(ideaId);
    expect((await request.get(base).then(response => response.json())).total).toBe(2);
  });
}

test("记下并加入：响应丢失后用同一命令重试，只创建一条记录",async({page,request,book})=>{
  await openIdeas(page,book.id);
  const base=`/api/file-workspaces/${book.id}/ideas`;
  const initial=await request.get(base).then(r=>r.json());
  let first=true; const commands:unknown[]=[];
  await page.route(`**${base}`,async route=>{
    if(route.request().method()!=="POST")return route.continue();
    commands.push(route.request().postDataJSON());
    if(first){first=false; const result=await route.fetch(); expect(result.ok()).toBeTruthy(); await route.abort("failed");}
    else await route.continue();
  });
  await page.getByLabel("记录灵感",{exact:true}).fill("失而复得的船票。");
  const add=page.getByRole("button",{name:"记下并加入对话",exact:true});
  await add.click(); await expect(page.locator(".idea-surface [role=alert]")).toBeVisible();
  await expect(page.getByLabel("记录灵感",{exact:true})).toHaveValue("失而复得的船票。");
  await expect(page.locator('.writing-chat button[aria-label^="查看引用的灵感"]')).toHaveCount(0);
  await add.click();
  await expect(page.getByRole("button",{name:"查看引用的灵感：失而复得的船票。",exact:true})).toBeVisible();
  expect(commands).toHaveLength(2); expect(commands[0]).toEqual(commands[1]);
  expect((await request.get(base).then(r=>r.json())).total).toBe(initial.total+1);
});

test("窗口和字号变化后仍可移动、收起与记录，不接管背景操作",async({page,book})=>{
  await openIdeas(page,book.id);
  for(const width of [1440,1024,640,390])for(const scale of [100,150]){
    await page.setViewportSize({width,height:width===1024?768:900});
    await page.getByRole("button",{name:"设置",exact:true}).click();
    const settings=page.getByRole("dialog",{name:"设置",exact:true});
    await settings.getByLabel("调整字体大小").selectOption(String(scale));
    await page.keyboard.press("Escape");
    const box=await page.locator(".idea-floating").boundingBox();
    expect(box!.x).toBeGreaterThanOrEqual(0);expect(box!.y).toBeGreaterThanOrEqual(0);
    expect(box!.x+box!.width).toBeLessThanOrEqual(width);
    expect(box!.y+box!.height).toBeLessThanOrEqual(width===1024?768:900);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await expect(page.getByRole("button",{name:"移动灵感面板",exact:true})).toBeInViewport();
    await page.getByLabel("记录灵感",{exact:true}).fill(`矩阵${width}-${scale}`);
    await page.getByRole("button",{name:"记下",exact:true}).click();
    await expect(page.getByLabel("记录灵感",{exact:true})).toHaveValue("");
    await shot(page,`floating-${width}-${scale}`);
  }
});

test("读取期间切换对话，迟到的引用不加入另一个草稿",async({page,request,book})=>{
  const stamp="2026-10-25T10:00:00+08:00";
  await page.route(`**/api/file-workspaces/${book.id}/conversations`,route=>route.fulfill({json:{items:["c1","c2"].map(id=>({id,title:id,created_at:stamp,updated_at:stamp})),recent_id:"c1",active_turn:null,revision:"r1"}}));
  await page.route(`**/api/file-workspaces/${book.id}/conversations/*/messages*`,route=>route.fulfill({json:{events:[],last_seq:0,active_turn:null,revision:"r1"}}));
  await openIdeas(page,book.id);
  await page.getByRole("button",{name:"移动灵感面板",exact:true}).press("Home");
  await page.getByLabel("选择对话",{exact:true}).selectOption("c1");
  const listing=await request.get(`/api/file-workspaces/${book.id}/ideas`).then(r=>r.json());
  let started!:()=>void,release!:()=>void;
  const pending=new Promise<void>(done=>{started=done;});
  const gate=new Promise<void>(done=>{release=done;});
  await page.route(`**/api/file-workspaces/${book.id}/ideas/${listing.items[0].idea_id}`,async route=>{started();await gate;await route.continue();});
  await page.locator(".idea-note").first().getByRole("button",{name:"加入对话",exact:true}).click();
  await pending;
  await page.getByLabel("选择对话",{exact:true}).selectOption("c2");
  release();
  await expect(page.locator(".idea-surface [role=alert]")).toContainText("IDEA_CHAT_CHANGED");
  await expect(page.locator('.writing-chat button[aria-label^="查看引用的灵感"]')).toHaveCount(0);
  await page.getByLabel("选择对话",{exact:true}).selectOption("c1");
  await expect(page.locator('.writing-chat button[aria-label^="查看引用的灵感"]')).toHaveCount(0);
});

test("已展开时旧链接仍定位原处，搜索、最近删除与撤销可达",async({page,request,book})=>{
  const base=`/api/file-workspaces/${book.id}/ideas`;
  const older=await request.post(base,{data:{body:"旧船票上有她的名字。",request_key:`old-link-${book.id}`}}).then(r=>r.json());
  await openIdeas(page,book.id);
  await page.goto(`/#/workspaces/${book.id}/ideas?entry=${older.idea_id}`);
  await expect(page.getByLabel("灵感正文",{exact:true})).toHaveValue(older.body);
  await page.locator(".idea-entry-more > summary").click();
  await page.getByRole("button",{name:"删除",exact:true}).click();
  await expect(page.getByRole("button",{name:"撤销删除",exact:true})).toBeVisible();
  await page.getByRole("button",{name:"撤销删除",exact:true}).click();
  await expect(page.getByLabel("灵感正文",{exact:true})).toHaveValue(older.body);
  await page.getByRole("button",{name:"收起修改",exact:true}).click();
  await page.getByRole("textbox",{name:"搜索灵感",exact:true}).fill("旧船票");
  await expect(page.locator(".idea-note")).toHaveCount(1);
  await page.getByRole("button",{name:"收起灵感",exact:true}).click();
  await page.getByRole("button",{name:"灵感",exact:true}).click();
  await expect(page.getByRole("textbox",{name:"搜索灵感",exact:true})).toHaveCount(0);
  await expect(page.locator(".idea-note")).toHaveCount(2);
});

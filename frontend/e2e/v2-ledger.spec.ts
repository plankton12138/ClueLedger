import { expect, test, type Page } from "@playwright/test";

/**
 * V03-10 设定集走查（真实 API + SQLite）：收拢后经右缘「设定集」标记进入抽屉，只读。
 *
 * 设定的增删改都在对话里由模型提议、经对话内「采用」落盘；本用例只验证只读展示：
 * 4 类分组（人物 / 线索 / 时间线 / 谜底）渲染，其余 kind（如关系）不显示，
 * 且面板内无任何编辑入口与待确认区块。
 *
 * 设定数据经真实 API 预置（前端已无写入口），这里只断言面板呈现。
 */

const STAMP = Date.now().toString(36);

async function seedProject(page: Page): Promise<{ pid: string; name: string }> {
  const name = `设定集走查-${STAMP}`;
  const created = await page.request.post("/api/v2/projects", { data: { name } });
  expect(created.ok()).toBeTruthy();
  const pid = (await created.json()).project_id as string;
  const seed = async (kind: string, fields: Record<string, unknown>) => {
    const res = await page.request.post(
      `/api/v2/projects/${pid}/ledger/entries`,
      {
        data: {
          kind,
          fields,
          request_id: `${kind}:${Math.random().toString(36).slice(2, 11)}`,
        },
      },
    );
    expect(res.ok(), `预置 ${kind}`).toBeTruthy();
    return (await res.json()).entry.id as string;
  };
  const shen = await seed("story.character", {
    name: "沈砚",
    identity: "海边客栈的客人",
  });
  const aiLin = await seed("story.character", { name: "艾琳", identity: "客栈前台" });
  // 字段按后端 schema：线索必填 statement、时间线必填 time、谜底必填 culprit_id。
  await seed("story.clue", { statement: "潮汐时刻表的边角写满铅笔小字" });
  await seed("story.timeline_event", { time: "凌晨一点十七分" });
  await seed("story.mystery_truth", {
    culprit_id: shen,
    motive: "客人是多年前的旧识",
  });
  // 非 4 类：关系（前端不渲染，用于验证只展示 4 类）。
  await seed("story.relationship", {
    source_id: shen,
    target_id: aiLin,
    relation_type: "旧识",
  });
  return { pid, name };
}

async function openDesk(page: Page, pid: string, name: string) {
  await page.goto("/");
  await page.evaluate(
    ({ id, nm }) => {
      localStorage.setItem(
        "hastings.v2.projects",
        JSON.stringify([{ projectId: id, name: nm, source: "created" }]),
      );
      localStorage.setItem("hastings.v2.current", id);
    },
    { id: pid, nm: name },
  );
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.getByLabel("写作台").waitFor({ state: "visible", timeout: 15000 });
}

test("设定集：只读展示 4 类，无编辑入口，其余 kind 不显示", async ({
  page,
}, info) => {
  const { pid, name } = await seedProject(page);
  await openDesk(page, pid, name);

  // 右缘「设定集」标记常驻；点开为抽屉。
  await page.getByRole("button", { name: "设定集", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "设定集" });
  await expect(drawer).toBeVisible();

  // 4 类分组，标签用类内固定中文名（不依赖后端 kind_label）。
  await expect(drawer.getByText(/人物 · 2/)).toBeVisible();
  await expect(drawer.getByText(/线索 · 1/)).toBeVisible();
  await expect(drawer.getByText(/时间线 · 1/)).toBeVisible();
  await expect(drawer.getByText(/谜底 · 1/)).toBeVisible();
  await expect(drawer.getByText(/关系 · /)).toHaveCount(0);

  // 只读：面板内无任何编辑入口与待确认区块。
  await expect(drawer.getByText("新增设定")).toHaveCount(0);
  await expect(drawer.getByText("编辑设定")).toHaveCount(0);
  await expect(drawer.getByText("待确认设定")).toHaveCount(0);

  // 条目可见（人物名）。
  await expect(drawer.getByText("沈砚")).toBeVisible();
  await page.screenshot({ path: info.outputPath("ledger-readonly.png") });

  // 抽屉可关（Esc）。
  await page.keyboard.press("Escape");
  await expect(drawer).toHaveCount(0);
});

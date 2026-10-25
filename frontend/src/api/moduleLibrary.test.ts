/**
 * 模块库客户端（0.8 D）。
 *
 * 钉住 URL、请求体与错误映射；真正的库行为由后端测试覆盖。条目正文的头部由服务端维护，
 * 这里只验证前端能取出正文，不重复解析字段。
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  createModule,
  deriveWorkspace,
  entryBody,
  fetchAssembly,
  fetchCompleteRecord,
  findModuleInfo,
  fetchPresets,
  listModules,
  postModuleUpdate,
  publishRevision,
  putAssembly,
} from "./moduleLibrary";
import { WorkspaceApiError } from "./workspace";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("完整原文读取（B06）", () => {
  const text = "正文🌧𠮷".repeat(9000);
  function serve(change?: (page: Record<string, unknown>, start: number) => void) {
    vi.stubGlobal("fetch", async (url: string) => {
      const start = Number(new URL(url, "http://test").searchParams.get("start") ?? 0);
      const points = Array.from(text);
      const end = Math.min(start + 16000, points.length);
      const page = { entry_id: "rain", title: "雨", kind: "note", aliases: [], fields: {}, text: points.slice(start, end).join(""), start, end, length: points.length, digest: "d", truncated: end < points.length, next_cursor: end < points.length ? end : null };
      change?.(page, start);
      return jsonResponse(page);
    });
  }
  it("跨多段按码点拼接，保持非BMP字符与全文", async () => {
    serve();
    const result = await fetchCompleteRecord("mod", "rev", "rain", { chars: Array.from(text).length, digest: "d" });
    expect(result.text).toBe(text);
    expect(result.truncated).toBe(false);
  });
  it.each(["cursor", "start", "digest", "length", "text", "entry_id", "truncated"])("分段%s不一致时拒绝作为可编辑原文", async (field) => {
    serve((page, start) => {
      if (start === 0) return;
      if (field === "cursor") page.next_cursor = start;
      else if (field === "start") page.start = 0;
      else if (field === "digest") page.digest = "changed";
      else if (field === "length") page.length = 1;
      else if (field === "text") page.text = "缺字";
      else if (field === "entry_id") page.entry_id = "other";
      else page.truncated = false;
    });
    await expect(fetchCompleteRecord("mod", "rev", "rain", { chars: Array.from(text).length, digest: "d" })).rejects.toThrow();
  });
  it("后续分段网络失败不返回截断内容", async () => {
    let calls = 0;
    serve();
    const fetchPage = fetch;
    vi.stubGlobal("fetch", async (...args: Parameters<typeof fetch>) => {
      if (++calls === 2) throw new TypeError("network");
      return fetchPage(...args);
    });
    await expect(fetchCompleteRecord("mod", "rev", "rain")).rejects.toThrow("network");
  });
});

describe("库列表", () => {
  it("预设响应完整保留服务端允许字段，不建立另一份允许字段表", async () => {
    const presets = [{ kind: "world", label: "世界设定", source_kinds: ["place"], organize_domain: null, allowed_fields: ["region", "scope"] }];
    vi.stubGlobal("fetch", async () => jsonResponse(presets));
    expect(await fetchPresets()).toEqual(presets);
  });
  it("精确模块链接跨页查找，不用当前筛选结果猜测head", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      calls.push(url);
      return jsonResponse(url.includes("cursor=c2") ? { items: [{ module_id: "mod_target", head: "rev_exact" }], next_cursor: null }
        : { items: [{ module_id: "mod_other" }], next_cursor: "c2" });
    });
    expect(await findModuleInfo("mod_target")).toMatchObject({ module_id: "mod_target", head: "rev_exact" });
    expect(calls).toHaveLength(2);
    expect(calls[1]).toContain("include_deleted=true");
  });
  it("查找目标遇到重复游标时结束并报告读取失败", async () => {
    vi.stubGlobal("fetch", async () => jsonResponse({ items: [], next_cursor: "repeat" }));
    await expect(findModuleInfo("mod_missing")).rejects.toThrow("MODULE_CURSOR_INVALID");
  });
  it("把类型、搜索、分页与已删除过滤拼成查询参数", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      calls.push(url);
      return jsonResponse({ items: [], total: 0, next_cursor: null });
    });

    await listModules({ kind: "world", q: "港口", limit: 10, includeDeleted: true });

    const url = calls[0];
    expect(url).toContain("/api/module-library/modules?");
    expect(url).toContain("kind=world");
    expect(url).toContain("q=%E6%B8%AF%E5%8F%A3");
    expect(url).toContain("limit=10");
    expect(url).toContain("include_deleted=true");
  });

  it("默认不带已删除过滤", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      calls.push(url);
      return jsonResponse({ items: [], total: 0, next_cursor: null });
    });

    await listModules();

    expect(calls[0]).not.toContain("include_deleted");
  });
});

describe("写入", () => {
  it("新建模块提交类型、名称、条目与请求键", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return jsonResponse({ module: { module_id: "mod_1" }, revision: { revision_id: "rev_1" } });
    });

    await createModule({
      kind: "characters",
      name: "主角团",
      entries: [{ entry_id: "lin", title: "林静", body: "法医。" }],
      request_key: "create-1",
    });

    expect(calls[0].url).toBe("/api/module-library/modules");
    expect(calls[0].init?.method).toBe("POST");
    expect(JSON.parse(String(calls[0].init?.body))).toMatchObject({
      kind: "characters",
      name: "主角团",
      request_key: "create-1",
    });
  });

  it("发布版本带上基版、head 与请求键", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return jsonResponse({ revision_id: "rev_2" });
    });

    await publishRevision("mod_1", {
      base_revision: "rev_1",
      expected_head: "rev_1",
      entries: [{ entry_id: "lin", title: "林静", body: "第二版。" }],
      request_key: "publish-1",
    });

    expect(calls[0].url).toBe("/api/module-library/modules/mod_1/revisions");
    expect(JSON.parse(String(calls[0].init?.body))).toMatchObject({
      base_revision: "rev_1",
      expected_head: "rev_1",
      request_key: "publish-1",
    });
  });
});

describe("装配", () => {
  it("读装配走作品前缀", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      calls.push(url);
      return jsonResponse({ bindings: [], links: [], digest: null });
    });

    await fetchAssembly("ws1");

    expect(calls[0]).toBe("/api/file-workspaces/ws1/assembly");
  });

  it("整份替换时把绑定与装配指纹一起提交", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return jsonResponse({ bindings: [], links: [], digest: "d1" });
    });

    await putAssembly("ws1", {
      bindings: [
        {
          module: { library_id: "lib_1", module_id: "mod_1", revision_id: "rev_1" },
          label: "主角团",
        },
      ],
      expected_digest: "d0",
      request_key: "bind-1",
    });

    expect(calls[0].init?.method).toBe("PUT");
    expect(JSON.parse(String(calls[0].init?.body))).toMatchObject({
      expected_digest: "d0",
      request_key: "bind-1",
      bindings: [{ module: { module_id: "mod_1", revision_id: "rev_1" } }],
    });
  });

  it("派生把名称与装配指纹提交到当前作品", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return jsonResponse({
        workspace: { id: "ws2", name: "另一条线", path: "/x", exists: true },
        assembly: { bindings: [], links: [], digest: "d2" },
        recovered: false,
      });
    });

    await deriveWorkspace("ws1", {
      name: "另一条线",
      expected_digest: "d1",
      request_key: "derive-1",
    });

    expect(calls[0].url).toBe("/api/file-workspaces/ws1/derive");
    expect(JSON.parse(String(calls[0].init?.body))).toMatchObject({
      name: "另一条线",
      expected_digest: "d1",
      request_key: "derive-1",
    });
  });

  it("保存并用于本作品提交装配指纹，并保留两步结果", async () => {
    vi.stubGlobal("fetch", async () =>
      jsonResponse({
        published: true,
        binding_updated: false,
        module: { module_id: "mod_1", revision_id: "rev_2" },
        error: { code: "ASSEMBLY_STALE_CONFLICT", why: "装配被改过", fix: "刷新" },
      }),
    );

    const result = await postModuleUpdate("ws1", {
      module_id: "mod_1",
      base_revision: "rev_1",
      expected_head: "rev_1",
      entries: [{ entry_id: "lin", title: "林静", body: "第二版。" }],
      expected_digest: "d0",
      request_key: "save-1",
    });

    expect(result.published).toBe(true);
    expect(result.binding_updated).toBe(false);
    expect(result.error?.code).toBe("ASSEMBLY_STALE_CONFLICT");
  });
});

describe("错误与正文", () => {
  it("把错误信封转成带 code 的异常", async () => {
    vi.stubGlobal("fetch", async () =>
      jsonResponse(
        { error: { code: "MODULE_HEAD_STALE_CONFLICT", why: "head 变了", fix: "刷新" } },
        409,
      ),
    );

    await expect(fetchAssembly("ws1")).rejects.toBeInstanceOf(WorkspaceApiError);
    await expect(fetchAssembly("ws1")).rejects.toMatchObject({
      code: "MODULE_HEAD_STALE_CONFLICT",
      status: 409,
    });
  });

  it("条目正文去掉受控头部", () => {
    const text = "---\nid: lin\nschema_version: 1\nkind: character\ntitle: 林静\n---\n法医，旧识。";
    expect(entryBody(text)).toBe("法医，旧识。");
    expect(entryBody("没有头部")).toBe("没有头部");
  });
});

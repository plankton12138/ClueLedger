/**
 * 文件工作区客户端（0.4 V04-4）。
 *
 * 这里只钉契约：URL 与请求体形状、错误信封转成带 code 的异常、SSE 分块解析。
 * 真正的服务端行为由后端测试与浏览器走查负责。
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchDefaultLocation,
  gitRecord,
  pickDirectory,
  listFiles,
  openWorkspace,
  readFile,
  startTurn,
  streamEvents,
  WorkspaceApiError,
} from "./workspace";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("默认存放位置", () => {
  it("用 GET /default-location 取服务端解析出的父目录", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      calls.push(url);
      return jsonResponse({ root: "C:////Users////me////Documents" });
    });

    const location = await fetchDefaultLocation();

    expect(calls).toEqual(["/api/file-workspaces/default-location"]);
    expect(location.root).toBe("C:////Users////me////Documents");
  });
});

describe("系统文件夹对话框", () => {
  it("用 POST /pick-directory 交给服务端弹窗，初始目录随请求带走", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return jsonResponse({ path: "C:////Users////me////Documents////我的小说" });
    });

    const picked = await pickDirectory("C:////Users////me////Documents");

    expect(calls[0]?.url).toBe("/api/file-workspaces/pick-directory");
    expect(calls[0]?.init?.method).toBe("POST");
    expect(calls[0]?.init?.body).toBe(JSON.stringify({ initial: "C:////Users////me////Documents" }));
    expect(picked.path).toBe("C:////Users////me////Documents////我的小说");
  });

  it("取消时 path 为 null", async () => {
    vi.stubGlobal("fetch", async () => jsonResponse({ path: null }));

    await expect(pickDirectory()).resolves.toEqual({ path: null });
  });
});

describe("请求形状", () => {
  it("列出目录与读取文件用 GET，路径做编码", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      calls.push(url);
      return jsonResponse(url.includes("/file?") ? { path: "正文/0001 雨夜.md" } : []);
    });

    await listFiles("w1");
    await readFile("w1", "正文/0001 雨夜.md");

    expect(calls[0]).toBe("/api/file-workspaces/w1/files");
    expect(calls[1]).toBe(
      `/api/file-workspaces/w1/file?path=${encodeURIComponent("正文/0001 雨夜.md")}`,
    );
  });

  it("新建与启动轮次用 POST 并带 JSON 体", async () => {
    const bodies: (string | undefined)[] = [];
    vi.stubGlobal("fetch", async (_url: string, init?: RequestInit) => {
      bodies.push(init?.body as string | undefined);
      return jsonResponse({ id: "w1", turn_id: "t1" });
    });

    await openWorkspace("C:/作品", true);
    await startTurn("w1", "c1", "写第一章", "k1");

    expect(JSON.parse(bodies[0]!)).toEqual({ path: "C:/作品", create: true });
    expect(JSON.parse(bodies[1]!)).toEqual({ text: "写第一章", request_key: "k1" });
  });

  it("补记历史用 POST 空体也会发送对象", async () => {
    let body: string | undefined;
    vi.stubGlobal("fetch", async (_url: string, init?: RequestInit) => {
      body = init?.body as string | undefined;
      return jsonResponse({ commit: null });
    });

    await gitRecord("w1");

    expect(JSON.parse(body!)).toEqual({ message: "" });
  });
});

describe("错误信封", () => {
  it("把 code/why/fix 转成带 code 的异常", async () => {
    vi.stubGlobal("fetch", async () =>
      jsonResponse(
        {
          error: {
            code: "FILE_STALE_CONFLICT",
            where: "files.write",
            why: "文件在这次写入之前已被改动",
            fix: "重新读取后再写",
            details: {},
          },
        },
        409,
      ),
    );

    const failure = readFile("w1", "正文/0001 雨夜.md");

    await expect(failure).rejects.toBeInstanceOf(WorkspaceApiError);
    await expect(failure).rejects.toMatchObject({
      code: "FILE_STALE_CONFLICT",
      status: 409,
      fix: "重新读取后再写",
    });
  });

  it("没有信封时给出 HTTP 码，不吞掉失败", async () => {
    vi.stubGlobal("fetch", async () => new Response("boom", { status: 500 }));

    await expect(listFiles("w1")).rejects.toMatchObject({ code: "HTTP_500" });
  });
});

describe("事件流解析", () => {
  function sseResponse(chunks: string[]): Response {
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    });
    return new Response(stream, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    });
  }

  it("跨分块拼接事件，只认 data 行", async () => {
    const first = 'id: 1\ndata: {"seq":1,"type":"turn_started","text":"写第一章"}\n\n';
    const second = 'id: 2\ndata: {"seq":2,"type":"run_finished","status":"completed"}\n\n';
    // 故意从 JSON 中间切开，验证缓冲区拼接。
    const cut = first.length - 20;
    vi.stubGlobal("fetch", async () =>
      sseResponse([first.slice(0, cut), first.slice(cut) + second]),
    );

    const events: number[] = [];
    for await (const event of streamEvents("w1", "c1", 0)) {
      events.push(event.seq);
    }

    expect(events).toEqual([1, 2]);
  });

  it("事件流未建立时抛可理解错误", async () => {
    vi.stubGlobal("fetch", async () => new Response("", { status: 503 }));

    const iterate = async () => {
      for await (const _event of streamEvents("w1", "c1", 0)) {
        // 不应有事件
      }
    };

    await expect(iterate()).rejects.toMatchObject({ code: "HTTP_503" });
  });
});

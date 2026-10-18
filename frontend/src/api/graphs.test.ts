/**
 * 图谱客户端契约（V03-6.4）：只读投影按 Commit 回读，「来源定位」按账本对象查证据。
 *
 * 覆盖三类请求：统一投影、人物图视图、线索图视图，以及按被锚定对象过滤的正文证据。
 * 查询参数只在有值时进 URL，空值不发空键。
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchCharacterGraph,
  fetchClueGraph,
  fetchEvidenceByTarget,
  fetchGraph,
} from "./graphs";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("统一投影", () => {
  it("默认请求作品图谱，无查询串", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe("/api/v2/projects/prj_1/graphs");
      return jsonResponse({ project_id: "prj_1", commit_id: "c1", nodes: [], edges: [] });
    });
    vi.stubGlobal("fetch", fetchMock);

    const graph = await fetchGraph("prj_1");
    expect(graph.commit_id).toBe("c1");
  });
});

describe("人物图视图", () => {
  it("视角、叙述位置与聚焦进查询串，空值不发键", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe(
        "/api/v2/projects/prj_1/graphs/characters?perspective=art_sis&at_chapter_id=art_ch2&focus=art_sis",
      );
      return jsonResponse({
        project_id: "prj_1",
        commit_id: "c1",
        perspective: { mode: "character", character_id: "art_sis", label: "姐姐视角" },
        position: { kind: "chapter", at_chapter_id: "art_ch2", at_beat_id: "beat_9", label: "第二章" },
        focus: { node_id: "art_sis", neighbor_ids: ["art_det"] },
        nodes: [],
        edges: [],
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const graph = await fetchCharacterGraph("prj_1", {
      perspective: "art_sis",
      at_chapter_id: "art_ch2",
      focus: "art_sis",
      at_beat_id: null,
    });
    expect(graph.perspective.label).toBe("姐姐视角");
    expect(graph.focus?.neighbor_ids).toEqual(["art_det"]);
  });
});

describe("线索图视图", () => {
  it("按节拍筛选并回报线索的计划/实现状态", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe(
        "/api/v2/projects/prj_1/graphs/clues?at_beat_id=beat_3",
      );
      return jsonResponse({
        project_id: "prj_1",
        commit_id: "c1",
        position: { kind: "beat", at_chapter_id: null, at_beat_id: "beat_3", label: "节拍三" },
        focus: null,
        clues: [
          {
            clue_id: "art_watch",
            statement: "怀表停走",
            necessity: true,
            points_to: ["art_fact"],
            planned_beats: [],
            disclosed_beats: ["beat_2"],
            status: "implemented",
            first_position: 1,
            within_position: true,
          },
        ],
        nodes: [],
        edges: [],
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const graph = await fetchClueGraph("prj_1", { at_beat_id: "beat_3" });
    expect(graph.clues[0].status).toBe("implemented");
    expect(graph.clues[0].within_position).toBe(true);
  });
});

describe("来源定位：按账本对象查正文证据", () => {
  it("用 target_id 过滤证据链接，返回引用片段与来源章", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe(
        "/api/v2/projects/prj_1/evidence-links?target_id=art_watch",
      );
      return jsonResponse({
        chapter_id: null,
        head_commit_id: "c1",
        evidence: [
          { evidence_id: "evd_1", target_id: "art_watch", chapter_id: "art_ch1", quote: "怀表停走" },
        ],
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const items = await fetchEvidenceByTarget("prj_1", "art_watch");
    expect(items[0].chapter_id).toBe("art_ch1");
    expect(items[0].quote).toBe("怀表停走");
  });
});

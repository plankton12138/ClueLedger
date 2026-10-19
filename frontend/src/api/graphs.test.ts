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
  fetchMethodGraph,
  fetchTimelineGraph,
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

describe("手法图视图", () => {
  it("回读真相文字、缺口与完整度，聚焦进查询串", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe(
        "/api/v2/projects/prj_1/graphs/method?focus=art_truth",
      );
      return jsonResponse({
        project_id: "prj_1",
        commit_id: "c1",
        focus: { node_id: "art_truth", neighbor_ids: ["art_sis"] },
        truths: [
          {
            truth_id: "art_truth",
            culprit_id: "art_sis",
            motive: "",
            method: "换药",
            cover_up: "",
            accomplice_ids: [],
            true_timeline: ["art_event"],
            missing_fields: ["motive", "cover_up"],
          },
        ],
        gaps: [
          {
            code: "METHOD_TEXT_NOT_RECORDED",
            where: "mystery_truth(art_truth).motive",
            why: "账本里还没有动机",
            fix: "在账本「真相」条目填写动机",
            truth_id: "art_truth",
          },
        ],
        completeness: { recorded: 1, total: 3, note: "只统计三段说明是否已写" },
        nodes: [],
        edges: [],
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const graph = await fetchMethodGraph("prj_1", { focus: "art_truth" });
    expect(graph.truths[0].method).toBe("换药");
    expect(graph.truths[0].missing_fields).toEqual(["motive", "cover_up"]);
    expect(graph.gaps[0].code).toBe("METHOD_TEXT_NOT_RECORDED");
    expect(graph.completeness.recorded).toBe(1);
  });
});

describe("时间线视图", () => {
  it("按角色分轨请求，回读真实事件与证词轨", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe(
        "/api/v2/projects/prj_1/graphs/timeline?group_by=character&focus=art_sis",
      );
      return jsonResponse({
        project_id: "prj_1",
        commit_id: "c1",
        group_by: "character",
        ordering: {
          story_time: "as_recorded",
          narrative_order: "testimony_only",
          note: "不排序，也不用章节先后代替故事时间",
        },
        testimony_note: "证词保留原模式",
        events: [
          {
            event_id: "art_e1",
            time_text: "八点到九点之间",
            time_status: "recorded",
            place_id: "art_room",
            participants: ["art_sis"],
            before_state: "",
            after_state: "",
          },
        ],
        tracks: [
          {
            key: "character:art_sis",
            kind: "character",
            id: "art_sis",
            label: "姐姐",
            event_ids: ["art_e1"],
          },
        ],
        testimonies: [
          {
            character_id: "art_det",
            label: "侦探",
            items: [
              {
                knowledge_id: "art_k1",
                character_id: "art_det",
                fact_id: "art_f1",
                mode: "misbelieves",
                mode_label: "误信",
                note: "他相信管家说的是十点",
                at_beat_id: null,
                position: null,
                claimed_event_id: "art_e1",
                claimed_event_time: "八点到九点之间",
              },
            ],
          },
        ],
        gaps: [],
        focus: { node_id: "art_sis", neighbor_ids: ["art_e1"] },
        nodes: [],
        edges: [],
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const graph = await fetchTimelineGraph("prj_1", { group_by: "character", focus: "art_sis" });
    expect(graph.group_by).toBe("character");
    expect(graph.tracks[0].event_ids).toEqual(["art_e1"]);
    expect(graph.testimonies[0].items[0].mode_label).toBe("误信");
    expect(graph.testimonies[0].items[0].position).toBeNull();
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

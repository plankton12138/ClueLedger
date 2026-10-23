/** 资料任务状态（0.7）：请求键生命周期、事件归并与结果回读。 */

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/storyMaterials", () => ({
  startMaterialTask: vi.fn(),
  listMaterialTasks: vi.fn(),
  fetchMaterialTask: vi.fn(),
  stopMaterialTask: vi.fn(),
}));

vi.mock("../api/workspace", () => ({
  streamEvents: vi.fn(),
  WorkspaceApiError: class WorkspaceApiError extends Error {
    constructor(
      readonly code: string,
      readonly why: string,
      readonly fix: string,
      readonly status: number,
    ) {
      super(why);
      this.name = "WorkspaceApiError";
    }
  },
}));

import {
  fetchMaterialTask,
  startMaterialTask,
  type MaterialScope,
  type MaterialTask,
} from "../api/storyMaterials";
import { streamEvents, WorkspaceApiError } from "../api/workspace";
import { useMaterialTasksStore } from "./materialTasks";

const mockedStart = vi.mocked(startMaterialTask);
const mockedDetail = vi.mocked(fetchMaterialTask);
const mockedStream = vi.mocked(streamEvents);

const scope: MaterialScope = {
  domain: "timeline",
  chapter_paths: ["正文/0001 雨夜.md"],
  entity_paths: [],
  as_of_chapter: 1,
  observed_digests: { "正文/0001 雨夜.md": "d1" },
};

const accepted = {
  turn_id: "t1",
  conversation_id: "c1",
  origin: "materials" as const,
  domain: "timeline" as const,
  label: "时间线",
  title: "整理时间线",
  model: "fake-model",
  request_key: "k1",
  scope: {
    domain: "timeline" as const,
    chapter_paths: scope.chapter_paths,
    entity_paths: [],
    as_of_chapter: 1,
  },
};

const finishedTask: MaterialTask = {
  turn_id: "t1",
  conversation_id: "c1",
  domain: "timeline",
  label: "时间线",
  scope: accepted.scope,
  status: "completed",
  model: "fake-model",
  started_at: "2026-10-23T00:00:00+08:00",
  published: 1,
  done: 1,
  total: 1,
  targets: [
    {
      path: scope.chapter_paths[0],
      chapter: 1,
      entity_path: "",
      outcome: "found",
      freshness: "valid",
    },
  ],
  unfinished: [],
};

describe("materialTasks store", () => {
  beforeEach(() => {
    mockedStart.mockReset();
    mockedDetail.mockReset();
    mockedStream.mockReset();
    useMaterialTasksStore.getState().reset("ws1");
  });

  it("受理失败后复用同一个请求键", async () => {
    mockedStart.mockRejectedValueOnce(new TypeError("network down"));
    const store = useMaterialTasksStore.getState();
    expect(await store.start("ws1", scope, "时间线")).toBeNull();
    expect(useMaterialTasksStore.getState().pendingKey?.signature).toBeTruthy();

    mockedStart.mockResolvedValueOnce(accepted);
    mockedStream.mockImplementation(async function* () {
      // 不产生事件：只验证请求键
    } as never);
    await useMaterialTasksStore.getState().start("ws1", scope, "时间线");

    const firstKey = (mockedStart.mock.calls[0][1] as { requestKey: string }).requestKey;
    const secondKey = (mockedStart.mock.calls[1][1] as { requestKey: string }).requestKey;
    expect(secondKey).toBe(firstKey);
    expect(useMaterialTasksStore.getState().pendingKey).toBeNull();
  });

  it("明确拒绝后换一次操作重新生成请求键", async () => {
    mockedStart.mockRejectedValueOnce(new WorkspaceApiError("MATERIAL_SCOPE_INVALID", "不行", "改", 400));
    await useMaterialTasksStore.getState().start("ws1", scope, "时间线");
    expect(useMaterialTasksStore.getState().pendingKey).toBeNull();
  });

  it("按事件累加进度，结束后以任务详情为准", async () => {
    mockedStart.mockResolvedValueOnce(accepted);
    mockedDetail.mockResolvedValueOnce(finishedTask);
    mockedStream.mockImplementation((async function* () {
      yield {
        type: "material_target_published",
        turn_id: "t1",
        domain: "timeline",
        path: scope.chapter_paths[0],
        entity: "",
        outcome: "found",
        published: 1,
      } as never;
      yield { type: "run_finished", turn_id: "t1", status: "completed" } as never;
    }) as never);

    const task = await useMaterialTasksStore.getState().start("ws1", scope, "时间线");
    expect(task?.status).toBe("accepted");
    await useMaterialTasksStore.getState().observe("ws1", "c1", "t1");

    const state = useMaterialTasksStore.getState();
    expect(state.tasks.t1.status).toBe("completed");
    expect(state.tasks.t1.done).toBe(1);
    expect(state.active.timeline).toBeUndefined();
    expect(state.revision).toBeGreaterThan(0);
  });
});

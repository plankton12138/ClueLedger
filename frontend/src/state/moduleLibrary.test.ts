/**
 * 模块库界面状态（0.8 D）。
 *
 * 验证查询与详情归属、完整读取的接线、保存快照及部分成功恢复；原文分段异常由客户端测试覆盖。
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/moduleLibrary", () => ({
  createModule: vi.fn(),
  deriveWorkspace: vi.fn(),
  entryBody: (text: string) => text,
  fetchAssembly: vi.fn(),
  fetchImportInfo: vi.fn(),
  fetchPresets: vi.fn(),
  fetchRecord: vi.fn(),
  fetchCompleteRecord: vi.fn(),
  findModuleInfo: vi.fn(),
  fetchRevision: vi.fn(),
  fetchRevisions: vi.fn(),
  forkModule: vi.fn(),
  importLegacyMaterials: vi.fn(),
  listModules: vi.fn(),
  postModuleUpdate: vi.fn(),
  publishRevision: vi.fn(),
  putAssembly: vi.fn(),
  requestKey: () => "key-1",
  restoreModule: vi.fn(),
  trashModule: vi.fn(),
}));

import {
  deriveWorkspace,
  fetchAssembly,
  fetchImportInfo,
  fetchPresets,
  fetchRecord,
  fetchCompleteRecord,
  findModuleInfo,
  fetchRevision,
  fetchRevisions,
  importLegacyMaterials,
  listModules,
  postModuleUpdate,
  publishRevision,
  putAssembly,
} from "../api/moduleLibrary";
import { useModuleLibraryStore } from "./moduleLibrary";
import { useModuleEditorStore, moduleDraftKey, draftEntries } from "./moduleEditor";

const listMock = vi.mocked(listModules);
const presetsMock = vi.mocked(fetchPresets);
const assemblyMock = vi.mocked(fetchAssembly);
const importInfoMock = vi.mocked(fetchImportInfo);
const importMock = vi.mocked(importLegacyMaterials);
const revisionMock = vi.mocked(fetchRevision);
const revisionsMock = vi.mocked(fetchRevisions);
const recordMock = vi.mocked(fetchRecord);
const completeRecordMock = vi.mocked(fetchCompleteRecord);
const updateMock = vi.mocked(postModuleUpdate);
const putMock = vi.mocked(putAssembly);
const deriveMock = vi.mocked(deriveWorkspace);

function reset(): void {
  listMock.mockReset();
  presetsMock.mockReset();
  assemblyMock.mockReset();
  importInfoMock.mockReset();
  importMock.mockReset();
  revisionMock.mockReset();
  revisionsMock.mockReset();
  recordMock.mockReset();
  completeRecordMock.mockReset();
  updateMock.mockReset();
  putMock.mockReset();
  deriveMock.mockReset();
  vi.mocked(publishRevision).mockReset();
  useModuleEditorStore.setState(useModuleEditorStore.getInitialState(), true);
  useModuleLibraryStore.setState(useModuleLibraryStore.getInitialState(), true);
}

const moduleInfo = {
  module_id: "mod_1",
  kind: "characters" as const,
  name: "主角团",
  head: "rev_1",
  deleted: false,
  created_at: "2026-10-24T00:00:00+00:00",
  updated_at: "2026-10-24T00:00:00+00:00",
  forked_from: null,
  revision_count: 1,
};

beforeEach(() => {
  reset();
  vi.mocked(findModuleInfo).mockImplementation(async id => ({ ...moduleInfo, module_id: id }));
  completeRecordMock.mockImplementation((...args) => fetchRecord(...args.slice(0, 3) as [string, string, string]));
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
describe("详情与列表归属（B05）", () => {
  const detail = (id: string) => ({ library_id: "lib_1", module_id: id, revision_id: "rev", kind: "characters" as const, name: id, entries: [] });
  it("A慢B快，A的成功不能覆盖B", async () => {
    const a = deferred<ReturnType<typeof detail>>();
    revisionMock.mockImplementation(id => id === "A" ? a.promise : Promise.resolve(detail(id)));
    revisionsMock.mockResolvedValue({ items: [], total: 0, next_cursor: null });
    useModuleLibraryStore.setState({ workspaceId: "ws1", open: true });
    const pending = useModuleLibraryStore.getState().selectModule("A", "rev");
    await vi.waitFor(() => expect(revisionMock).toHaveBeenCalledTimes(1));
    await useModuleLibraryStore.getState().selectModule("B", "rev");
    a.resolve(detail("A")); await pending;
    expect(useModuleLibraryStore.getState().detail?.module_id).toBe("B");
  });
  it("A→B→A，第一轮A的失败不能覆盖最后A的loading与错误", async () => {
    const first = deferred<ReturnType<typeof detail>>();
    revisionMock.mockReturnValueOnce(first.promise).mockResolvedValue(detail("B")).mockResolvedValue(detail("A"));
    revisionsMock.mockResolvedValue({ items: [], total: 0, next_cursor: null });
    useModuleLibraryStore.setState({ workspaceId: "ws1", open: true });
    const pending = useModuleLibraryStore.getState().selectModule("A", "rev");
    await vi.waitFor(() => expect(revisionMock).toHaveBeenCalledTimes(1));
    await useModuleLibraryStore.getState().selectModule("B", "rev");
    await useModuleLibraryStore.getState().selectModule("A", "rev");
    first.reject(new Error("old failure")); await pending;
    expect(useModuleLibraryStore.getState().error).toBeNull();
    expect(useModuleLibraryStore.getState().detail?.module_id).toBe("A");
  });
  it("关闭后旧结果不能重新装入详情", async () => {
    const a = deferred<ReturnType<typeof detail>>();
    revisionMock.mockReturnValue(a.promise);
    revisionsMock.mockResolvedValue({ items: [], total: 0, next_cursor: null });
    useModuleLibraryStore.setState({ workspaceId: "ws1", open: true });
    const pending = useModuleLibraryStore.getState().selectModule("A", "rev");
    useModuleLibraryStore.getState().closeDrawer();
    a.resolve(detail("A")); await pending;
    expect(useModuleLibraryStore.getState().detail).toBeNull();
  });
  it("搜索A→B→A，第一轮列表结果不能替换最后查询", async () => {
    const a = deferred<{ items: typeof moduleInfo[]; total: number; next_cursor: null }>();
    listMock.mockReturnValueOnce(a.promise).mockResolvedValue({ items: [], total: 0, next_cursor: null });
    useModuleLibraryStore.setState({ workspaceId: "ws1", query: "A" });
    const pending = useModuleLibraryStore.getState().refresh();
    useModuleLibraryStore.setState({ query: "B" }); await useModuleLibraryStore.getState().refresh();
    useModuleLibraryStore.setState({ query: "A" }); await useModuleLibraryStore.getState().refresh();
    a.resolve({ items: [moduleInfo], total: 1, next_cursor: null }); await pending;
    expect(useModuleLibraryStore.getState().modules).toEqual([]);
  });
});

describe("浏览", () => {
  it("按类型与搜索刷新列表，并保留分页游标", async () => {
    listMock.mockResolvedValue({ items: [moduleInfo], total: 1, next_cursor: "c2" });
    useModuleLibraryStore.setState({ workspaceId: "ws1", kind: "world", query: "港口" });

    await useModuleLibraryStore.getState().refresh();

    expect(listMock).toHaveBeenCalledWith({
      kind: "world",
      q: "港口",
      limit: 20,
      includeDeleted: false,
    });
    const state = useModuleLibraryStore.getState();
    expect(state.modules.map((item) => item.module_id)).toEqual(["mod_1"]);
    expect(state.cursor).toBe("c2");
  });

  it("包含已删除的过滤会传给接口", async () => {
    listMock.mockResolvedValue({ items: [], total: 0, next_cursor: null });
    useModuleLibraryStore.setState({ workspaceId: "ws1", includeDeleted: true });

    await useModuleLibraryStore.getState().refresh();

    expect(listMock).toHaveBeenCalledWith(expect.objectContaining({ includeDeleted: true }));
  });
});

describe("选中版本", () => {
  it("读取该版本的目录与每条正文", async () => {
    revisionsMock.mockResolvedValue({
      items: [{ revision_id: "rev_1", parent: null, created_at: "t", entry_ids: ["lin"] }],
      total: 1,
      next_cursor: null,
    });
    revisionMock.mockResolvedValue({
      library_id: "lib_1",
      module_id: "mod_1",
      revision_id: "rev_1",
      kind: "characters",
      name: "主角团",
      entries: [
        {
          entry_id: "lin",
          kind: "character",
          title: "林静",
          aliases: [],
          fields: {},
          chars: 10,
          digest: "d",
        },
      ],
    });
    recordMock.mockResolvedValue({
      entry_id: "lin",
      kind: "character",
      title: "林静",
      aliases: [],
      fields: {},
      text: "法医。",
      start: 0,
      end: 3,
      length: 3,
      digest: "d",
      truncated: false,
      next_cursor: null,
    });
    useModuleLibraryStore.setState({ workspaceId: "ws1" });

    await useModuleLibraryStore.getState().selectModule("mod_1", "rev_1");

    const state = useModuleLibraryStore.getState();
    expect(state.selectedRevisionId).toBe("rev_1");
    expect(state.bodies).toEqual({ lin: "法医。" });
    expect(state.revisions).toHaveLength(1);
  });

  it("没有 head 的模块只保留选中状态，不发请求", async () => {
    useModuleLibraryStore.setState({
      workspaceId: "ws1",
      modules: [{ ...moduleInfo, head: null }],
    });

    await useModuleLibraryStore.getState().selectModule("mod_1");

    expect(revisionMock).not.toHaveBeenCalled();
    expect(useModuleLibraryStore.getState().detail).toBeNull();
  });
});

describe("保存并用于本作品", () => {
  it("发布成功但绑定失败时保留发布结果并提示重试", async () => {
    updateMock.mockResolvedValue({
      published: true,
      binding_updated: false,
      module: {
        library_id: "lib_1",
        module_id: "mod_1",
        revision_id: "rev_2",
      },
      error: {
        code: "ASSEMBLY_STALE_CONFLICT",
        where: "assembly",
        why: "装配被改过",
        fix: "刷新后重试",
      },
    });
    assemblyMock.mockResolvedValue({
      schema_version: 1,
      library_id: "lib_1",
      digest: "d1",
      bindings: [],
      links: [],
    });
    listMock.mockResolvedValue({ items: [moduleInfo], total: 1, next_cursor: null });
    revisionMock.mockResolvedValue({
      library_id: "lib_1",
      module_id: "mod_1",
      revision_id: "rev_2",
      kind: "characters",
      name: "主角团",
      entries: [],
    });
    revisionsMock.mockResolvedValue({ items: [], total: 0, next_cursor: null });
    useModuleLibraryStore.setState({
      workspaceId: "ws1",
      selectedModuleId: "mod_1",
      selectedRevisionId: "rev_1",
      modules: [moduleInfo],
      detail: {
        library_id: "lib_1",
        module_id: "mod_1",
        revision_id: "rev_1",
        kind: "characters",
        name: "主角团",
        entries: [],
      },
      assembly: {
        schema_version: 1,
        library_id: "lib_1",
        digest: "d1",
        bindings: [],
        links: [],
      },
    });

    const ok = await useModuleLibraryStore.getState().saveAndUse([
      { entry_id: "lin", title: "林静", body: "第二版。" },
    ]);

    expect(ok).toBe(false);
    const state = useModuleLibraryStore.getState();
    expect(state.notice).toContain("绑定没有更新");
    expect(state.error).toContain("刷新后重试");
    expect(updateMock).toHaveBeenCalledWith(
      "ws1",
      expect.objectContaining({ expected_digest: "d1", base_revision: "rev_1" }),
    );
  });
});

describe("派生", () => {
  it("用当前组合新建作品后返回新作品的标识", async () => {
    deriveMock.mockResolvedValue({
      workspace: { id: "ws2", name: "另一条线", path: "/tmp/另一条线", exists: true },
      assembly: { schema_version: 1, library_id: "lib_1", digest: "d2", bindings: [], links: [] },
      recovered: false,
    });
    useModuleLibraryStore.setState({
      workspaceId: "ws1",
      assembly: {
        schema_version: 1,
        library_id: "lib_1",
        digest: "d1",
        bindings: [],
        links: [],
      },
    });

    const created = await useModuleLibraryStore.getState().derive("另一条线");

    expect(created?.id).toBe("ws2");
    expect(deriveMock).toHaveBeenCalledWith(
      "ws1",
      expect.objectContaining({ name: "另一条线", expected_digest: "d1" }),
    );
    expect(useModuleLibraryStore.getState().notice).toContain("另一条线");
  });

  it("派生时把替换版本原样交给后端", async () => {
    deriveMock.mockResolvedValue({
      workspace: { id: "ws2", name: "另一条线", path: "/tmp/另一条线", exists: true },
      assembly: { schema_version: 1, library_id: "lib_1", digest: "d2", bindings: [], links: [] },
      recovered: false,
    });
    useModuleLibraryStore.setState({
      workspaceId: "ws1",
      assembly: {
        schema_version: 1,
        library_id: "lib_1",
        digest: "d1",
        bindings: [
          {
            id: "bnd_1",
            label: "主角团",
            module: { library_id: "lib_1", module_id: "mod_1", revision_id: "rev_1" },
          },
        ],
        links: [],
      },
    });

    await useModuleLibraryStore.getState().derive("另一条线", { mod_1: "rev_2" });

    expect(deriveMock).toHaveBeenCalledWith(
      "ws1",
      expect.objectContaining({ replacements: { mod_1: "rev_2" } }),
    );
  });
});

describe("装配", () => {
  it("装入时保留已有绑定并带上库标识", async () => {
    putMock.mockResolvedValue({
      schema_version: 1,
      library_id: "lib_1",
      digest: "d2",
      bindings: [],
      links: [],
    });
    useModuleLibraryStore.setState({
      workspaceId: "ws1",
      assembly: {
        schema_version: 1,
        library_id: "lib_1",
        digest: "d1",
        bindings: [
          {
            id: "bnd_1",
            label: "反派",
            module: { library_id: "lib_1", module_id: "mod_2", revision_id: "rev_9" },
          },
        ],
        links: [["bnd_1", "bnd_2"]],
      },
    });

    await useModuleLibraryStore.getState().bind("mod_1", "rev_1");

    const body = putMock.mock.calls[0][1];
    expect(body.expected_digest).toBe("d1");
    expect(body.links).toEqual([["bnd_1", "bnd_2"]]);
    expect(body.bindings).toHaveLength(2);
    expect(body.bindings[1].module).toEqual({
      library_id: "lib_1",
      module_id: "mod_1",
      revision_id: "rev_1",
    });
  });

  it("卸载只提交剩余的绑定", async () => {
    putMock.mockResolvedValue({
      schema_version: 1,
      library_id: "lib_1",
      digest: "d3",
      bindings: [],
      links: [],
    });
    useModuleLibraryStore.setState({
      workspaceId: "ws1",
      assembly: {
        schema_version: 1,
        library_id: "lib_1",
        digest: "d2",
        bindings: [
          {
            id: "bnd_1",
            label: "主角团",
            module: { library_id: "lib_1", module_id: "mod_1", revision_id: "rev_1" },
          },
          {
            id: "bnd_2",
            label: "反派",
            module: { library_id: "lib_1", module_id: "mod_2", revision_id: "rev_9" },
          },
        ],
        links: [
          ["bnd_1", "bnd_2"],
          ["bnd_2", "bnd_3"],
          ["bnd_3", "bnd_4"],
        ],
      },
    });

    await useModuleLibraryStore.getState().unbind("bnd_1");

    const body = putMock.mock.calls[0][1];
    expect(body.bindings.map((item) => item.id)).toEqual(["bnd_2"]);
    // 只移除与被卸绑定有关的关系，其他关联保留。
    expect(body.links).toEqual([
      ["bnd_2", "bnd_3"],
      ["bnd_3", "bnd_4"],
    ]);
  });

  it("切换版本时保留已有关系", async () => {
    putMock.mockResolvedValue({
      schema_version: 1,
      library_id: "lib_1",
      digest: "d3",
      bindings: [],
      links: [],
    });
    useModuleLibraryStore.setState({
      workspaceId: "ws1",
      assembly: {
        schema_version: 1,
        library_id: "lib_1",
        digest: "d2",
        bindings: [
          {
            id: "bnd_1",
            label: "主角团",
            module: { library_id: "lib_1", module_id: "mod_1", revision_id: "rev_1" },
          },
          {
            id: "bnd_2",
            label: "反派",
            module: { library_id: "lib_1", module_id: "mod_2", revision_id: "rev_9" },
          },
        ],
        links: [["bnd_1", "bnd_2"]],
      },
    });

    await useModuleLibraryStore.getState().upgrade("bnd_1", "rev_2");

    const body = putMock.mock.calls[0][1];
    expect(body.links).toEqual([["bnd_1", "bnd_2"]]);
    expect(body.bindings[0].module.revision_id).toBe("rev_2");
  });

  it("调整顺序只交换位置并保留关联", async () => {
    putMock.mockResolvedValue({
      schema_version: 1,
      library_id: "lib_1",
      digest: "d3",
      bindings: [],
      links: [],
    });
    useModuleLibraryStore.setState({
      workspaceId: "ws1",
      assembly: {
        schema_version: 1,
        library_id: "lib_1",
        digest: "d2",
        bindings: [
          {
            id: "bnd_1",
            label: "主角团",
            module: { library_id: "lib_1", module_id: "mod_1", revision_id: "rev_1" },
          },
          {
            id: "bnd_2",
            label: "反派",
            module: { library_id: "lib_1", module_id: "mod_2", revision_id: "rev_9" },
          },
        ],
        links: [["bnd_1", "bnd_2"]],
      },
    });

    await useModuleLibraryStore.getState().reorder("bnd_2", -1);

    const body = putMock.mock.calls[0][1];
    expect(body.bindings.map((item) => item.id)).toEqual(["bnd_2", "bnd_1"]);
    expect(body.links).toEqual([["bnd_1", "bnd_2"]]);
    expect(useModuleLibraryStore.getState().notice).toContain("顺序");
  });
});

describe("导入旧资料", () => {
  it("用当前导入清单指纹提交，并汇总新建、更新与无变化", async () => {
    importMock.mockResolvedValue({
      items: [
        {
          path: "设定集/线索.md",
          module_id: "mod_1",
          revision_id: "rev_1",
          kind: "clues",
          name: "线索（导入）",
          action: "created",
        },
        {
          path: "设定集/真相.md",
          module_id: "mod_2",
          revision_id: "rev_2",
          kind: "truth",
          name: "真相（导入）",
          action: "unchanged",
        },
      ],
      assembly: { schema_version: 1, library_id: "lib_1", digest: "d2", bindings: [], links: [] },
      digest: "import-d2",
    });
    importInfoMock.mockResolvedValue({ digest: "import-d2", items: [] });
    listMock.mockResolvedValue({ items: [], total: 0, next_cursor: null });
    useModuleLibraryStore.setState({
      workspaceId: "ws1",
      importInfo: { digest: "import-d1", items: [] },
      assembly: { schema_version: 1, library_id: "lib_1", digest: "d1", bindings: [], links: [] },
    });

    await useModuleLibraryStore.getState().importLegacy();

    expect(importMock).toHaveBeenCalledWith(
      "ws1",
      expect.objectContaining({ expected_digest: "import-d1" }),
    );
    const state = useModuleLibraryStore.getState();
    expect(state.notice).toContain("新建 1");
    expect(state.notice).toContain("无变化 1");
    expect(state.importInfo?.digest).toBe("import-d2");
  });
});

describe("打开抽屉", () => {
  it("同时读取预设、模块列表与装配", async () => {
    presetsMock.mockResolvedValue([
      {
        kind: "characters",
        label: "人物",
        source_kinds: ["character"],
        organize_domain: "characters",
        allowed_fields: ["roles", "surface", "revealed", "planned"],
      },
    ]);
    listMock.mockResolvedValue({ items: [], total: 0, next_cursor: null });
    assemblyMock.mockResolvedValue({
      schema_version: 1,
      library_id: null,
      digest: null,
      bindings: [],
      links: [],
    });
    importInfoMock.mockResolvedValue({ digest: null, items: [] });

    await useModuleLibraryStore.getState().openDrawer("ws1");

    const state = useModuleLibraryStore.getState();
    expect(state.open).toBe(true);
    expect(state.workspaceId).toBe("ws1");
    expect(state.presets).toHaveLength(1);
    expect(listMock).toHaveBeenCalled();
    expect(assemblyMock).toHaveBeenCalledWith("ws1");
  });
});


describe("发布命令与部分成功恢复（UI08/UI09）", () => {
  const baseDetail = { library_id: "lib_1", module_id: "mod_1", revision_id: "rev_1", kind: "characters" as const, name: "主角团", entries: [{ entry_id: "lin", kind: "character", title: "林静", fields: {}, aliases: [], chars: 3, digest: "d" }] };
  const original = { id: "target", label: "主角", module: { library_id: "lib_1", module_id: "mod_1", revision_id: "rev_1" } };
  const unrelated = { id: "other", label: "港口", module: { library_id: "lib_1", module_id: "mod_2", revision_id: "rev_9" } };
  const assembly = { schema_version: 1, library_id: "lib_1", digest: "d1", bindings: [original, unrelated], links: [["target", "other"]] as [string, string][] };
  function prepare() {
    useModuleEditorStore.getState().ensure(baseDetail, { lin: "原文" });
    const key = moduleDraftKey(baseDetail);
    useModuleEditorStore.getState().edit(key, rows => rows.map(row => ({ ...row, body: "提交内容" })));
    useModuleLibraryStore.setState({ workspaceId: "ws1", open: true, modules: [moduleInfo], selectedModuleId: "mod_1", selectedRevisionId: "rev_1", detail: baseDetail, assembly });
    listMock.mockResolvedValue({ items: [{ ...moduleInfo, head: "rev_2" }], total: 1, next_cursor: null });
    revisionMock.mockResolvedValue({ ...baseDetail, revision_id: "rev_2", entries: [] });
    revisionsMock.mockResolvedValue({ items: [], total: 0, next_cursor: null });
    assemblyMock.mockResolvedValue(assembly);
    return key;
  }
  it("网络结果未知，同键同体重试；保存中新增输入在新基版上保留", async () => {
    const key = prepare();
    const entries = draftEntries(useModuleEditorStore.getState().drafts[key].rows);
    vi.mocked(publishRevision).mockRejectedValueOnce(new TypeError("network")).mockResolvedValue({ revision_id: "rev_2", parent: "rev_1", created_at: "t", entry_ids: ["lin"] });
    await useModuleLibraryStore.getState().saveToLibrary(entries);
    const pending = useModuleLibraryStore.getState().saveToLibrary(entries);
    useModuleEditorStore.getState().edit(key, rows => rows.map(row => ({ ...row, body: "后来输入" })));
    await pending;
    expect(vi.mocked(publishRevision).mock.calls[0]).toEqual(vi.mocked(publishRevision).mock.calls[1]);
    const next = moduleDraftKey({ ...baseDetail, revision_id: "rev_2" });
    expect(useModuleEditorStore.getState().drafts[next].rows[0].body).toBe("后来输入");
    expect(useModuleEditorStore.getState().drafts[next].baseline[0].body).toBe("提交内容");
  });
  it("保存中切换作品，成功与失败都不替换新作品的详情和错误", async () => {
    const key = prepare();
    const deferredResult = deferred<{ revision_id: string; parent: string; created_at: string; entry_ids: string[] }>();
    vi.mocked(publishRevision).mockReturnValue(deferredResult.promise);
    const pending = useModuleLibraryStore.getState().saveToLibrary(draftEntries(useModuleEditorStore.getState().drafts[key].rows));
    useModuleLibraryStore.setState({ workspaceId: "ws2", session: 1, selectedModuleId: "B", detail: { ...baseDetail, module_id: "B" }, error: "新作品错误" });
    deferredResult.resolve({ revision_id: "rev_2", parent: "rev_1", created_at: "t", entry_ids: ["lin"] }); await pending;
    expect(useModuleLibraryStore.getState()).toMatchObject({ workspaceId: "ws2", detail: { module_id: "B" }, error: "新作品错误" });
    expect(useModuleEditorStore.getState().drafts[moduleDraftKey({ ...baseDetail, revision_id: "rev_2" })]).toBeDefined();
  });
  it("已发布后只重试装配，保留其他绑定、标签、links与最新digest", async () => {
    const key = prepare();
    updateMock.mockResolvedValue({ published: true, binding_updated: false, module: { ...original.module, revision_id: "rev_2" } });
    await useModuleLibraryStore.getState().saveAndUse(draftEntries(useModuleEditorStore.getState().drafts[key].rows));
    const command = Object.values(useModuleLibraryStore.getState().publications)[0];
    const latest = { ...assembly, digest: "d3", bindings: [original, { ...unrelated, label: "新标签" }] };
    assemblyMock.mockResolvedValue(latest);
    putMock.mockResolvedValue({ ...latest, digest: "d4" });
    await useModuleLibraryStore.getState().retryApplication(command.id);
    expect(updateMock).toHaveBeenCalledTimes(1);
    expect(publishRevision).not.toHaveBeenCalled();
    expect(putMock.mock.calls[0][1]).toMatchObject({ expected_digest: "d3", links: assembly.links, bindings: [{ id: "target", label: "主角", module: { revision_id: "rev_2" } }, { id: "other", label: "新标签", module: unrelated.module }] });
  });
  it("目标已被更换时阻止重试覆盖，不再发布", async () => {
    const key = prepare();
    updateMock.mockResolvedValue({ published: true, binding_updated: false, module: { ...original.module, revision_id: "rev_2" } });
    await useModuleLibraryStore.getState().saveAndUse(draftEntries(useModuleEditorStore.getState().drafts[key].rows));
    const command = Object.values(useModuleLibraryStore.getState().publications)[0];
    assemblyMock.mockResolvedValue({ ...assembly, bindings: [{ ...original, module: { ...original.module, revision_id: "someone-else" } }, unrelated] });
    expect(await useModuleLibraryStore.getState().retryApplication(command.id)).toBe(false);
    expect(putMock).not.toHaveBeenCalled();
    expect(useModuleLibraryStore.getState().error).toContain("ASSEMBLY_TARGET_CONFLICT");
  });
  it("装配网络结果未知时重试保持同一PUT请求体", async () => {
    const key = prepare();
    updateMock.mockResolvedValue({ published: true, binding_updated: false, module: { ...original.module, revision_id: "rev_2" } });
    await useModuleLibraryStore.getState().saveAndUse(draftEntries(useModuleEditorStore.getState().drafts[key].rows));
    const command = Object.values(useModuleLibraryStore.getState().publications)[0];
    putMock.mockRejectedValueOnce(new TypeError("network")).mockResolvedValue(assembly);
    await useModuleLibraryStore.getState().retryApplication(command.id);
    await useModuleLibraryStore.getState().retryApplication(command.id);
    expect(putMock.mock.calls[0]).toEqual(putMock.mock.calls[1]);
    expect(updateMock).toHaveBeenCalledTimes(1);
  });
});

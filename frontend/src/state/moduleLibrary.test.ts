/**
 * 模块库界面状态（0.8 D）。
 *
 * 只验证三件容易出错的事：查询参数跟着类型与搜索走、选中版本会把条目正文读进来、
 * 「保存并用于本作品」在绑定失败时保留发布结果而不是报成整体失败。
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
  fetchRevision,
  fetchRevisions,
  importLegacyMaterials,
  listModules,
  postModuleUpdate,
  putAssembly,
} from "../api/moduleLibrary";
import { useModuleLibraryStore } from "./moduleLibrary";

const listMock = vi.mocked(listModules);
const presetsMock = vi.mocked(fetchPresets);
const assemblyMock = vi.mocked(fetchAssembly);
const importInfoMock = vi.mocked(fetchImportInfo);
const importMock = vi.mocked(importLegacyMaterials);
const revisionMock = vi.mocked(fetchRevision);
const revisionsMock = vi.mocked(fetchRevisions);
const recordMock = vi.mocked(fetchRecord);
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
  updateMock.mockReset();
  putMock.mockReset();
  deriveMock.mockReset();
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

beforeEach(reset);

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

/**
 * 全局模块库抽屉（0.8 D）。
 *
 * 左边浏览与搜索库里的实例，右边查看并编辑选中的版本。装入、卸下、切换版本都只改
 * 当前作品的装配清单；「保存到模块库」与「保存并用于本作品」是两个不同的动作，
 * 后者才会把新版本用到当前作品。
 *
 * 抽屉不复制库原文：切换版本时按精确版本读取条目，编辑只改正文与标题，发布时整份提交。
 */

import { useEffect, useState } from "react";

import type { EntryInput, ModuleKind } from "../../api/moduleLibrary";
import { navigate } from "../../state/navigation";
import {
  bindingLabel,
  bindingProblems,
  useModuleLibraryStore,
} from "../../state/moduleLibrary";
import { useWorkspaceStore } from "../../state/workspace";

const KIND_LABEL: Record<ModuleKind, string> = {
  characters: "人物",
  world: "世界设定",
  clues: "线索与伏笔",
  truth: "真相与事件经过",
  timeline: "时间线",
  plot: "剧情安排",
  reference: "自由资料",
};

const KIND_OPTIONS: ModuleKind[] = [
  "characters",
  "world",
  "clues",
  "truth",
  "timeline",
  "plot",
  "reference",
];

interface EntryRow {
  entry_id: string;
  kind: string;
  title: string;
  body: string;
  aliases: string[];
  fields: Record<string, unknown>;
}

function newEntryId(): string {
  return `entry_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
}

export default function ModuleDrawer() {
  const store = useModuleLibraryStore();
  const open = store.open;
  const [search, setSearch] = useState("");
  const [rows, setRows] = useState<EntryRow[]>([]);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({
    kind: "characters" as ModuleKind,
    name: "",
    title: "",
    body: "",
  });
  const [forking, setForking] = useState(false);
  const [forkName, setForkName] = useState("");
  const [confirmTrash, setConfirmTrash] = useState(false);
  const [deriving, setDeriving] = useState(false);
  const [deriveName, setDeriveName] = useState("");

  useEffect(() => {
    if (!open) {
      setSearch("");
      setCreating(false);
      setConfirmTrash(false);
      setForking(false);
      setDeriving(false);
      setDeriveName("");
    }
  }, [open]);

  useEffect(() => {
    if (!store.detail) {
      setRows([]);
      return;
    }
    setRows(
      store.detail.entries.map((entry) => ({
        entry_id: entry.entry_id,
        kind: entry.kind,
        title: entry.title,
        body: store.bodies[entry.entry_id] ?? "",
        aliases: entry.aliases,
        fields: entry.fields,
      })),
    );
  }, [store.detail, store.bodies]);

  if (!open) return null;

  const info = store.modules.find((item) => item.module_id === store.selectedModuleId);
  const atHead = Boolean(info && store.selectedRevisionId === info.head);
  const bound = store.assembly?.bindings.find(
    (item) => item.module.module_id === store.selectedModuleId,
  );

  function entries(): EntryInput[] {
    return rows.map((row) => ({
      entry_id: row.entry_id,
      kind: row.kind,
      title: row.title,
      body: row.body,
      aliases: row.aliases,
      fields: row.fields,
    }));
  }

  function addEntry(): void {
    setRows((current) => [
      ...current,
      { entry_id: newEntryId(), kind: "", title: "", body: "", aliases: [], fields: {} },
    ]);
  }

  function removeEntry(entryId: string): void {
    setRows((current) => current.filter((row) => row.entry_id !== entryId));
  }

  async function submitCreate(): Promise<void> {
    const ok = await store.create(form);
    if (ok) {
      setCreating(false);
      setForm({ kind: form.kind, name: "", title: "", body: "" });
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/30" role="dialog" aria-label="模块库">
      <div className="flex h-full w-full max-w-5xl flex-col border-l border-[#cfc7ae] bg-[#fbf8ef] shadow-2xl">
        <header className="flex flex-none items-center gap-2 border-b border-[#e2dbc6] px-4 py-3">
          <h2 className="font-serif text-base font-bold text-[#3f3a2c]">模块库</h2>
          <span className="text-[11px] text-[#8a8371]">全局内容，按版本装入作品</span>
          <div className="flex-1" />
          <button
            className="btn btn-gray px-2 py-1 text-[11px]"
            title="把本作品已有的线索、真相与人物小传导入全局库，并绑定精确版本"
            disabled={store.busy}
            onClick={() => void store.importLegacy()}
          >
            导入旧资料
          </button>
          <button className="btn btn-gray px-2 py-1 text-[11px]" onClick={() => store.closeDrawer()}>
            关闭
          </button>
        </header>

        {(store.error || store.notice) && (
          <div
            className={`flex flex-none items-center gap-3 border-b px-4 py-2 text-[11px] ${
              store.error
                ? "border-red-edge bg-red-bg text-red"
                : "border-amber-edge bg-amber-bg text-amber"
            }`}
          >
            <span className="min-w-0 flex-1">{store.error ?? store.notice}</span>
            <button className="underline" onClick={() => store.clearNotice()}>
              知道了
            </button>
          </div>
        )}

        <div className="flex min-h-0 flex-1">
          <aside className="flex w-80 flex-none flex-col border-r border-[#e2dbc6]">
            {store.assembly && store.assembly.bindings.length > 0 && (
              <section className="flex-none border-b border-[#e2dbc6] px-3 py-2">
                <h3 className="text-[11px] font-bold text-[#6b6555]">
                  本作品已装（{store.assembly.bindings.length}）
                </h3>
                <ul className="mt-1 space-y-1">
                  {store.assembly.bindings.map((binding) => {
                    const problems = bindingProblems(binding);
                    return (
                      <li key={binding.id} className="flex items-center gap-2 text-[11px]">
                        <button
                          className="min-w-0 flex-1 truncate text-left hover:underline"
                          onClick={() =>
                            void store.selectModule(
                              binding.module.module_id,
                              binding.module.revision_id,
                            )
                          }
                        >
                          {bindingLabel(binding)}
                          <span className="ml-1 text-[10px] text-[#8a8371]">
                            {binding.kind ? KIND_LABEL[binding.kind as ModuleKind] : ""}
                          </span>
                        </button>
                        {problems && <span className="text-[10px] text-amber">{problems}</span>}
                        <button
                          className="text-[10px] text-[#8a8371] underline"
                          onClick={() => void store.unbind(binding.id)}
                        >
                          卸下
                        </button>
                      </li>
                    );
                  })}
                </ul>
                <div className="mt-2 flex flex-none items-center gap-1">
                  {deriving ? (
                    <>
                      <input
                        aria-label="新作品名称"
                        className="well-input min-w-0 flex-1 px-2 py-1 text-[11px]"
                        value={deriveName}
                        placeholder="新作品目录名"
                        onChange={(event) => setDeriveName(event.target.value)}
                      />
                      <button
                        className="btn btn-gold px-2 py-1 text-[11px]"
                        disabled={store.busy || !deriveName.trim()}
                        onClick={() => {
                          void store.derive(deriveName).then(async (created) => {
                            if (!created) return;
                            setDeriving(false);
                            setDeriveName("");
                            // 登记列表会多一条：先刷新，否则顶栏仍把新作品标成「目录已不在」。
                            await useWorkspaceStore.getState().refreshBindings();
                            await useWorkspaceStore.getState().selectWorkspace(created.id);
                            navigate({ kind: "writing", workspaceId: created.id });
                            store.closeDrawer();
                          });
                        }}
                      >
                        创建
                      </button>
                      <button
                        className="text-[10px] text-[#8a8371] underline"
                        onClick={() => setDeriving(false)}
                      >
                        取消
                      </button>
                    </>
                  ) : (
                    <button
                      className="text-[10px] text-[#6b6555] underline"
                      onClick={() => setDeriving(true)}
                    >
                      用当前组合新建作品
                    </button>
                  )}
                </div>
              </section>
            )}

            <div className="flex flex-none flex-wrap gap-1 px-3 py-2">
              <button
                className={`rounded px-2 py-0.5 text-[11px] ${
                  store.kind === "all" ? "bg-[#3f3a2c] text-white" : "bg-[#efe9d8] text-[#4b4737]"
                }`}
                onClick={() => store.setKind("all")}
              >
                全部
              </button>
              {KIND_OPTIONS.map((kind) => (
                <button
                  key={kind}
                  className={`rounded px-2 py-0.5 text-[11px] ${
                    store.kind === kind ? "bg-[#3f3a2c] text-white" : "bg-[#efe9d8] text-[#4b4737]"
                  }`}
                  onClick={() => store.setKind(kind)}
                >
                  {KIND_LABEL[kind]}
                </button>
              ))}
            </div>

            <div className="flex flex-none items-center gap-2 px-3 pb-2">
              <input
                aria-label="搜索模块"
                className="well-input min-w-0 flex-1 px-2 py-1 text-[11px]"
                placeholder="搜索名称、条目标题或别名"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") store.setQuery(search);
                  if (event.key === "Escape") {
                    setSearch("");
                    store.setQuery("");
                  }
                }}
              />
              <button className="btn btn-gray px-2 py-1 text-[11px]" onClick={() => store.setQuery(search)}>
                查找
              </button>
            </div>

            <label className="flex flex-none items-center gap-2 px-3 pb-2 text-[10px] text-[#6b6555]">
              <input
                type="checkbox"
                checked={store.includeDeleted}
                onChange={(event) => store.setIncludeDeleted(event.target.checked)}
              />
              显示已删除
            </label>

            <div className="min-h-0 flex-1 overflow-auto px-2 pb-3">
              <ul className="space-y-0.5">
                {store.modules.map((item) => (
                  <li key={item.module_id}>
                    <button
                      className={`w-full rounded px-2 py-1 text-left text-[12px] hover:bg-[#efe9d8] ${
                        item.module_id === store.selectedModuleId ? "bg-[#efe9d8]" : ""
                      }`}
                      onClick={() => void store.selectModule(item.module_id)}
                    >
                      <span className="block truncate font-bold">{item.name}</span>
                      <span className="block text-[10px] text-[#8a8371]">
                        {KIND_LABEL[item.kind]} · {item.revision_count ?? 1} 个版本
                        {item.deleted ? " · 已删除" : ""}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
              {store.loading && <p className="px-2 py-2 text-[11px] text-[#8a8371]">读取中…</p>}
              {!store.loading && store.modules.length === 0 && (
                <p className="px-2 py-2 text-[11px] text-[#8a8371]">
                  这里还没有内容。新建一份人物、世界设定或线索。
                </p>
              )}
              {store.cursor && (
                <button
                  className="mt-2 w-full px-2 py-1 text-[11px] text-[#6b6555] underline"
                  onClick={() => void store.loadMore()}
                >
                  加载更多（共 {store.total} 项）
                </button>
              )}
            </div>

            <div className="flex-none border-t border-[#e2dbc6] px-3 py-2">
              <button
                className="btn btn-gold w-full px-3 py-1 text-[11px]"
                onClick={() => {
                  setCreating(true);
                  setConfirmTrash(false);
                }}
              >
                新建模块
              </button>
            </div>
          </aside>

          <section className="flex min-w-0 flex-1 flex-col">
            {creating ? (
              <div className="min-h-0 flex-1 space-y-3 overflow-auto px-5 py-4 text-[12px]">
                <h3 className="font-serif text-sm font-bold">新建模块</h3>
                <div className="flex gap-2">
                  <label className="text-[11px] text-[#6b6555]">
                    类型
                    <select
                      className="well-input ml-2 px-2 py-1 text-[11px]"
                      value={form.kind}
                      onChange={(event) =>
                        setForm({ ...form, kind: event.target.value as ModuleKind })
                      }
                    >
                      {KIND_OPTIONS.map((kind) => (
                        <option key={kind} value={kind}>
                          {KIND_LABEL[kind]}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <label className="block text-[11px] text-[#6b6555]">
                  名称
                  <input
                    className="well-input mt-1 w-full px-2 py-1 text-[12px]"
                    value={form.name}
                    onChange={(event) => setForm({ ...form, name: event.target.value })}
                    placeholder="例如：主角团、海上城市、密室调查"
                  />
                </label>
                <label className="block text-[11px] text-[#6b6555]">
                  第一条的标题
                  <input
                    className="well-input mt-1 w-full px-2 py-1 text-[12px]"
                    value={form.title}
                    onChange={(event) => setForm({ ...form, title: event.target.value })}
                  />
                </label>
                <label className="block text-[11px] text-[#6b6555]">
                  正文
                  <textarea
                    className="well-input mt-1 h-40 w-full px-2 py-1 text-[12px] leading-6"
                    value={form.body}
                    onChange={(event) => setForm({ ...form, body: event.target.value })}
                  />
                </label>
                <div className="flex gap-2">
                  <button
                    className="btn btn-gold px-3 py-1 text-[11px]"
                    disabled={store.busy || !form.name.trim() || !form.body.trim()}
                    onClick={() => void submitCreate()}
                  >
                    新建并发布第一版
                  </button>
                  <button className="btn btn-gray px-3 py-1 text-[11px]" onClick={() => setCreating(false)}>
                    取消
                  </button>
                </div>
              </div>
            ) : store.detail ? (
              <div className="flex min-h-0 flex-1 flex-col">
                <div className="flex flex-none flex-wrap items-center gap-2 border-b border-[#e2dbc6] px-5 py-3">
                  <h3 className="font-serif text-sm font-bold">{store.detail.name}</h3>
                  <span className="text-[11px] text-[#8a8371]">{KIND_LABEL[store.detail.kind]}</span>
                  <div className="flex-1" />
                  <label className="text-[10px] text-[#6b6555]">
                    版本
                    <select
                      className="well-input ml-1 px-1 py-0.5 text-[10px]"
                      value={store.selectedRevisionId ?? ""}
                      onChange={(event) => void store.selectRevision(event.target.value)}
                    >
                      {store.revisions.map((revision, index) => (
                        <option key={revision.revision_id} value={revision.revision_id}>
                          {index === 0 ? "最新" : revision.created_at.slice(0, 16)}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>

                <div className="min-h-0 flex-1 space-y-3 overflow-auto px-5 py-4">
                  {store.detailLoading && <p className="text-[11px] text-[#8a8371]">读取版本…</p>}
                  {rows.map((row) => (
                    <article key={row.entry_id} className="rounded border border-[#e2dbc6] bg-white/60 p-2">
                      <div className="flex items-center gap-2">
                        <input
                          aria-label="条目标题"
                          className="well-input min-w-0 flex-1 px-2 py-1 text-[12px] font-bold"
                          value={row.title}
                          onChange={(event) =>
                            setRows((current) =>
                              current.map((item) =>
                                item.entry_id === row.entry_id
                                  ? { ...item, title: event.target.value }
                                  : item,
                              ),
                            )
                          }
                        />
                        <button
                          className="text-[10px] text-[#8a8371] underline"
                          onClick={() => removeEntry(row.entry_id)}
                        >
                          移除
                        </button>
                      </div>
                      <textarea
                        aria-label="条目正文"
                        className="well-input mt-1 h-28 w-full px-2 py-1 text-[12px] leading-6"
                        value={row.body}
                        onChange={(event) =>
                          setRows((current) =>
                            current.map((item) =>
                              item.entry_id === row.entry_id
                                ? { ...item, body: event.target.value }
                                : item,
                            ),
                          )
                        }
                      />
                    </article>
                  ))}
                  <button className="text-[11px] text-[#6b6555] underline" onClick={addEntry}>
                    添加条目
                  </button>
                </div>

                <footer className="flex flex-none flex-wrap items-center gap-2 border-t border-[#e2dbc6] px-5 py-3">
                  {bound ? (
                    <button
                      className="btn btn-gray px-3 py-1 text-[11px]"
                      disabled={store.busy || bound.module.revision_id === store.selectedRevisionId}
                      onClick={() =>
                        store.selectedRevisionId &&
                        void store.upgrade(bound.id, store.selectedRevisionId)
                      }
                    >
                      切换本作品版本
                    </button>
                  ) : (
                    <button
                      className="btn btn-gray px-3 py-1 text-[11px]"
                      disabled={store.busy || !store.selectedRevisionId || Boolean(info?.deleted)}
                      onClick={() =>
                        store.selectedModuleId &&
                        store.selectedRevisionId &&
                        void store.bind(store.selectedModuleId, store.selectedRevisionId)
                      }
                    >
                      装入本作品
                    </button>
                  )}
                  <button
                    className="btn btn-gray px-3 py-1 text-[11px]"
                    disabled={store.busy || !atHead || Boolean(info?.deleted)}
                    title={atHead ? "发布新版本；任何作品都不会自动跟随" : "只有最新版本可以继续保存"}
                    onClick={() => void store.saveToLibrary(entries())}
                  >
                    保存到模块库
                  </button>
                  <button
                    className="btn btn-gold px-3 py-1 text-[11px]"
                    disabled={store.busy || !atHead || Boolean(info?.deleted)}
                    onClick={() => void store.saveAndUse(entries())}
                  >
                    保存并用于本作品
                  </button>
                  <div className="flex-1" />
                  {forking ? (
                    <span className="flex items-center gap-1">
                      <input
                        aria-label="变体名称"
                        className="well-input px-2 py-1 text-[11px]"
                        value={forkName}
                        onChange={(event) => setForkName(event.target.value)}
                        placeholder="变体名称"
                      />
                      <button
                        className="btn btn-gray px-2 py-1 text-[11px]"
                        disabled={store.busy || !forkName.trim()}
                        onClick={() => {
                          void store.fork(forkName);
                          setForking(false);
                          setForkName("");
                        }}
                      >
                        另存
                      </button>
                    </span>
                  ) : (
                    <button className="text-[10px] text-[#6b6555] underline" onClick={() => setForking(true)}>
                      另存变体
                    </button>
                  )}
                  {info?.deleted ? (
                    <button
                      className="text-[10px] text-[#6b6555] underline"
                      onClick={() => void store.restoreSelected()}
                    >
                      恢复
                    </button>
                  ) : confirmTrash ? (
                    <span className="flex items-center gap-1 text-[10px] text-red">
                      确认删除？
                      <button className="underline" onClick={() => void store.trashSelected()}>
                        删除
                      </button>
                      <button className="underline" onClick={() => setConfirmTrash(false)}>
                        取消
                      </button>
                    </span>
                  ) : (
                    <button className="text-[10px] text-[#8a8371] underline" onClick={() => setConfirmTrash(true)}>
                      删除
                    </button>
                  )}
                </footer>
              </div>
            ) : (
              <div className="grid flex-1 place-items-center px-6 text-center text-[12px] text-[#8a8371]">
                <div>
                  <p>选择左边的一份内容查看版本。</p>
                  <p className="mt-1 text-[11px]">
                    模块库是全局的：同一份人物可以装进两个作品，各自固定使用的版本。
                  </p>
                </div>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}

/**
 * 状态面板（0.6 V06-E2 / E4）。
 *
 * 一个抽屉三个标签，对应三种「截至第 N 章」的事实：
 *
 * - 身份：读者已经知道什么（来源：小传声明 + 正文核对记录）；
 * - 人物状态：这一章结束时谁在哪、是否还活着、立场（只来自原文核对）；
 * - 时间：每章的故事时间锚点，缺锚点的章单独标出来。
 *
 * 面板只读：改设定仍然是改小传元信息，界面不提供第二个事实来源。核对按钮只是发起一次
 * 只读任务，结果落在作品状态目录里。
 */

import { useCallback, useEffect, useState } from "react";

import {
  fetchClueState,
  fetchDisclosureState,
  fetchRosterState,
  fetchTimelineState,
  type ClueEntry,
  type DisclosureEntity,
  type RosterCharacter,
} from "../../api/workspace";
import { useConversationsStore } from "../../state/conversations";
import { useWorkspaceStore } from "../../state/workspace";

const ROLE_LABEL: Record<string, string> = {
  detective: "侦探",
  assistant: "助手",
  culprit: "凶手",
  suspect: "嫌疑人",
  victim: "受害者",
};

type Tab = "identity" | "roster" | "timeline" | "clues";

interface Props {
  open: boolean;
  onClose: () => void;
}

function facts(items: { fact: string; chapter: number; source?: "text" | "declared" }[], tone: string) {
  return items.map((item) => (
    <span key={`${item.chapter}:${item.fact}`} className={`text-[10px] ${tone}`}>
      {item.fact}（第{item.chapter}章）
      {item.source === "text" && <span className="ml-0.5 text-mute">·原文</span>}
    </span>
  ));
}

function Checkbox({ checked, onToggle, label }: { checked: boolean; onToggle: () => void; label: string }) {
  return (
    <input
      type="checkbox"
      className="mr-1"
      checked={checked}
      onChange={onToggle}
      aria-label={`选择 ${label}`}
    />
  );
}

function EntityRow({
  entity,
  checked,
  onToggle,
}: {
  entity: DisclosureEntity;
  checked: boolean;
  onToggle: () => void;
}) {
  const roles = entity.roles.map((role) => ROLE_LABEL[role] ?? role);
  return (
    <li className="border-b border-panel-edge py-2 last:border-b-0">
      <div className="flex flex-wrap items-baseline gap-1">
        <Checkbox checked={checked} onToggle={onToggle} label={entity.name} />
        <span className="text-xs font-bold">{entity.name}</span>
        {entity.aliases.length > 0 && (
          <span className="text-[10px] text-mute">（{entity.aliases.join("、")}）</span>
        )}
        {roles.length > 0 && <span className="text-[10px] text-sub">{roles.join("、")}</span>}
        {entity.surface && <span className="text-[10px] text-sub">｜{entity.surface}</span>}
        {entity.overdue && <span className="text-[10px] text-amber">计划章已到</span>}
        {entity.record_status === "stale" && (
          <span className="text-[10px] text-amber">核对已过期</span>
        )}
      </div>
      <div className="mt-1 flex flex-col gap-0.5">
        <p className="flex flex-wrap gap-2">
          <span className="text-[10px] text-mute">读者已知</span>
          {entity.revealed.length > 0 ? (
            facts(entity.revealed, "text-green")
          ) : (
            <span className="text-[10px] text-mute">（还没有披露）</span>
          )}
        </p>
        {entity.upcoming.length > 0 && (
          <p className="flex flex-wrap gap-2">
            <span className="text-[10px] text-mute">声明未到章</span>
            {facts(entity.upcoming, "text-amber")}
            <span className="text-[10px] text-mute">（正文写到该章后生效）</span>
          </p>
        )}
        {entity.planned.length > 0 && (
          <p className="flex flex-wrap gap-2">
            <span className="text-[10px] text-mute">计划披露</span>
            {facts(entity.planned, "text-sub")}
            <span className="text-[10px] text-mute">（不进写作上下文）</span>
          </p>
        )}
        {entity.warnings.map((warning) => (
          <p key={warning} className="text-[10px] text-amber">
            {warning}
          </p>
        ))}
      </div>
    </li>
  );
}

function RosterRow({
  character,
  checked,
  onToggle,
}: {
  character: RosterCharacter;
  checked: boolean;
  onToggle: () => void;
}) {
  return (
    <li className="border-b border-panel-edge py-2 last:border-b-0">
      <div className="flex flex-wrap items-baseline gap-1">
        <Checkbox checked={checked} onToggle={onToggle} label={character.name} />
        <span className="text-xs font-bold">{character.name}</span>
        {character.chapter === null ? (
          <span className="text-[10px] text-mute">（还没有核对记录）</span>
        ) : (
          <>
            <span className="text-[10px] text-sub">｜{character.location || "位置未记"}</span>
            <span className={`text-[10px] ${character.alive ? "text-sub" : "text-red"}`}>
              ｜{character.alive ? "在世" : "已死亡"}
            </span>
            {character.stance && (
              <span className="text-[10px] text-sub">｜立场：{character.stance}</span>
            )}
            <span className="text-[10px] text-mute">（第{character.chapter}章）</span>
          </>
        )}
        {character.stale && <span className="text-[10px] text-amber">有记录已失效</span>}
      </div>
      {character.warnings.length > 0 && (
        <div className="mt-1 flex flex-col gap-0.5">
          {character.warnings.map((warning) => (
            <p key={warning} className="text-[10px] text-amber">
              {warning}
            </p>
          ))}
        </div>
      )}
    </li>
  );
}

function useLoad<T>(loader: (workspaceId: string) => Promise<T>, workspaceId: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!workspaceId) return;
    setLoading(true);
    try {
      setData(await loader(workspaceId));
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "状态读取失败");
    } finally {
      setLoading(false);
    }
  }, [loader, workspaceId]);

  return { data, loading, error, load };
}

function IdentityTab({ workspaceId, active }: { workspaceId: string | null; active: boolean }) {
  const { data, loading, error, load } = useLoad(fetchDisclosureState, workspaceId);
  const [selected, setSelected] = useState<string[]>([]);
  const send = useConversationsStore((state) => state.send);
  const pending = useConversationsStore((state) => state.pendingKey);

  useEffect(() => {
    if (active) void load();
  }, [active, load]);

  const toggle = (path: string) =>
    setSelected((current) =>
      current.includes(path) ? current.filter((item) => item !== path) : [...current, path],
    );

  const submit = async () => {
    if (selected.length === 0 || !data) return;
    const names = data.entities
      .filter((entity) => selected.includes(entity.path))
      .map((entity) => entity.name);
    await send(`核对人物身份：${names.join("、")}`, undefined, {
      purpose: "update_disclosure",
      indexPaths: selected,
    });
    setSelected([]);
    await load();
  };

  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <p className="min-w-0 flex-1 text-[10px] text-mute">
          正文写到第 {data?.current_chapter ?? 0} 章；写作时只带「读者已知」这一栏。
        </p>
        <button
          className="btn btn-gray px-2 py-0.5 text-[11px]"
          disabled={selected.length === 0 || pending !== null}
          onClick={() => void submit()}
        >
          核对选中{selected.length > 0 ? `（${selected.length}）` : ""}
        </button>
        <button className="btn btn-gray px-2 py-0.5 text-[11px]" onClick={() => void load()}>
          刷新
        </button>
      </div>
      {loading && <p className="text-mute">正在读取…</p>}
      {error && <p className="text-red">{error}</p>}
      {!loading && data && (
        <>
          {data.conflicts.length > 0 && (
            <ul className="mb-2 flex flex-col gap-1 border border-amber-edge bg-amber-bg px-2 py-1">
              {data.conflicts.map((conflict, index) => (
                <li
                  key={`${conflict.path}:${conflict.kind}:${index}`}
                  className="text-[10px] text-amber"
                >
                  {conflict.label}｜{conflict.name}：{conflict.detail}
                </li>
              ))}
            </ul>
          )}
          {data.entities.length === 0 ? (
            <p className="text-mute">
              还没有人物小传。在「设定集/角色小传」下新建文件后，这里会列出身份声明。
            </p>
          ) : (
            <ul>
              {data.entities.map((entity) => (
                <EntityRow
                  key={entity.path}
                  entity={entity}
                  checked={selected.includes(entity.path)}
                  onToggle={() => toggle(entity.path)}
                />
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}

function RosterTab({ workspaceId, active }: { workspaceId: string | null; active: boolean }) {
  const { data, loading, error, load } = useLoad(fetchRosterState, workspaceId);
  const [selected, setSelected] = useState<string[]>([]);
  const send = useConversationsStore((state) => state.send);
  const pending = useConversationsStore((state) => state.pendingKey);

  useEffect(() => {
    if (active) void load();
  }, [active, load]);

  const toggle = (path: string) =>
    setSelected((current) =>
      current.includes(path) ? current.filter((item) => item !== path) : [...current, path],
    );

  const submit = async () => {
    if (selected.length === 0 || !data) return;
    const names = data.characters
      .filter((item) => selected.includes(item.path))
      .map((item) => item.name);
    await send(`核对人物状态与时间：${names.join("、")}`, undefined, {
      purpose: "update_state",
      indexPaths: selected,
    });
    setSelected([]);
    await load();
  };

  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <p className="min-w-0 flex-1 text-[10px] text-mute">
          位置、生死与立场只来自原文核对；没有记录的章节按「未知」处理，不替作者推断。
        </p>
        <button
          className="btn btn-gray px-2 py-0.5 text-[11px]"
          disabled={selected.length === 0 || pending !== null}
          onClick={() => void submit()}
        >
          核对选中{selected.length > 0 ? `（${selected.length}）` : ""}
        </button>
        <button className="btn btn-gray px-2 py-0.5 text-[11px]" onClick={() => void load()}>
          刷新
        </button>
      </div>
      {loading && <p className="text-mute">正在读取…</p>}
      {error && <p className="text-red">{error}</p>}
      {!loading && data && (
        <>
          {data.characters.length === 0 ? (
            <p className="text-mute">还没有人物小传，无法核对人物状态。</p>
          ) : (
            <ul>
              {data.characters.map((character) => (
                <RosterRow
                  key={character.path}
                  character={character}
                  checked={selected.includes(character.path)}
                  onToggle={() => toggle(character.path)}
                />
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}

function TimelineTab({ workspaceId, active }: { workspaceId: string | null; active: boolean }) {
  const { data, loading, error, load } = useLoad(fetchTimelineState, workspaceId);

  useEffect(() => {
    if (active) void load();
  }, [active, load]);

  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <p className="min-w-0 flex-1 text-[10px] text-mute">
          一章一个锚点，时间写原文的说法；缺锚点的章在下面单列，写作时按「时间未知」处理。
        </p>
        <button className="btn btn-gray px-2 py-0.5 text-[11px]" onClick={() => void load()}>
          刷新
        </button>
      </div>
      {loading && <p className="text-mute">正在读取…</p>}
      {error && <p className="text-red">{error}</p>}
      {!loading && data && (
        <>
          {data.entries.length === 0 ? (
            <p className="text-mute">
              还没有时间锚点。在「人物状态」里勾选人物并核对，模型会同时记录时间。
            </p>
          ) : (
            <ul>
              {data.entries.map((entry) => (
                <li
                  key={entry.chapter}
                  className="flex flex-wrap items-baseline gap-1 border-b border-panel-edge py-1.5 last:border-b-0"
                >
                  <span className="text-xs font-bold">第{entry.chapter}章</span>
                  <span className="text-[11px]">{entry.when}</span>
                  {entry.span && <span className="text-[10px] text-sub">｜{entry.span}</span>}
                  {entry.location && (
                    <span className="text-[10px] text-sub">｜{entry.location}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
          {data.missing.length > 0 && (
            <p className="mt-2 text-[10px] text-amber">
              没有时间锚点的章：{data.missing.map((chapter) => `第${chapter}章`).join("、")}
            </p>
          )}
        </>
      )}
    </div>
  );
}

function ClueRow({ entry }: { entry: ClueEntry }) {
  const tone =
    entry.status === "open" ? (entry.overdue ? "text-amber" : "text-sub") : "text-mute";
  return (
    <li className="border-b border-panel-edge py-1.5 last:border-b-0">
      <div className="flex flex-wrap items-baseline gap-1">
        <span className="text-xs">{entry.clue}</span>
        <span className={`text-[10px] ${tone}`}>
          {entry.label}
          {entry.overdue && "｜已超期"}
        </span>
      </div>
      <div className="mt-0.5 flex flex-wrap gap-2 text-[10px] text-mute">
        <span>第{entry.planted_chapter}章埋下</span>
        {entry.due_chapter !== null && <span>计划第{entry.due_chapter}章回收</span>}
        {entry.resolution_chapter !== null && <span>第{entry.resolution_chapter}章回收</span>}
        {entry.supports && <span>指向：{entry.supports}</span>}
      </div>
    </li>
  );
}

function CluesTab({ workspaceId, active }: { workspaceId: string | null; active: boolean }) {
  const { data, loading, error, load } = useLoad(fetchClueState, workspaceId);

  useEffect(() => {
    if (active) void load();
  }, [active, load]);

  const open = data?.entries.filter((entry) => entry.status === "open") ?? [];
  const closed = data?.entries.filter((entry) => entry.status !== "open") ?? [];

  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <p className="min-w-0 flex-1 text-[10px] text-mute">
          只有未回收的线索进入写作上下文，超期项排在最前；线索不是真相，回收与否以原文为准。
        </p>
        <button className="btn btn-gray px-2 py-0.5 text-[11px]" onClick={() => void load()}>
          刷新
        </button>
      </div>
      {loading && <p className="text-mute">正在读取…</p>}
      {error && <p className="text-red">{error}</p>}
      {!loading && data && (
        <>
          {data.entries.length === 0 ? (
            <p className="text-mute">
              还没有线索台账。在「人物状态」里勾选人物并核对，模型会同时登记这一章的线索。
            </p>
          ) : (
            <>
              <h3 className="mb-1 text-[11px] font-bold">
                未回收{open.length > 0 ? `（${open.length}）` : ""}
              </h3>
              {open.length === 0 ? (
                <p className="mb-2 text-[10px] text-mute">没有未回收的线索。</p>
              ) : (
                <ul className="mb-2">
                  {open.map((entry, index) => (
                    <ClueRow key={`${entry.clue}:${index}`} entry={entry} />
                  ))}
                </ul>
              )}
              {closed.length > 0 && (
                <>
                  <h3 className="mb-1 text-[11px] font-bold">已回收与已废弃</h3>
                  <ul>
                    {closed.map((entry, index) => (
                      <ClueRow key={`${entry.clue}:${index}`} entry={entry} />
                    ))}
                  </ul>
                </>
              )}
            </>
          )}
          {data.warnings.map((warning) => (
            <p key={warning} className="mt-2 text-[10px] text-amber">
              {warning}
            </p>
          ))}
        </>
      )}
    </div>
  );
}

export default function StatePanel({ open, onClose }: Props) {
  const workspaceId = useWorkspaceStore((state) => state.currentId);
  const [tab, setTab] = useState<Tab>("identity");

  if (!open) return null;

  const tabs: { key: Tab; label: string }[] = [
    { key: "identity", label: "身份" },
    { key: "roster", label: "人物状态" },
    { key: "timeline", label: "时间线" },
    { key: "clues", label: "线索" },
  ];

  return (
    <div className="fixed inset-0 z-30 flex justify-end bg-black/30" role="dialog" aria-label="作品状态">
      <aside className="flex h-full w-[min(520px,92vw)] flex-col bg-panel shadow-xl">
        <div className="flex flex-none items-center gap-2 border-b border-panel-edge px-3 py-2">
          <h2 className="min-w-0 flex-1 text-sm font-bold">作品状态</h2>
          <button className="btn btn-gray px-2 py-0.5 text-[11px]" onClick={onClose}>
            关闭
          </button>
        </div>

        <div className="flex flex-none gap-1 border-b border-panel-edge px-3 py-1">
          {tabs.map((item) => (
            <button
              key={item.key}
              className={`px-2 py-0.5 text-[11px] ${
                tab === item.key ? "font-bold text-ink" : "text-mute"
              }`}
              onClick={() => setTab(item.key)}
            >
              {item.label}
            </button>
          ))}
        </div>

        <div className="min-h-0 flex-1 overflow-auto px-3 py-2 text-[11px]">
          {tab === "identity" && <IdentityTab workspaceId={workspaceId} active />}
          {tab === "roster" && <RosterTab workspaceId={workspaceId} active />}
          {tab === "timeline" && <TimelineTab workspaceId={workspaceId} active />}
          {tab === "clues" && <CluesTab workspaceId={workspaceId} active />}
        </div>
      </aside>
    </div>
  );
}

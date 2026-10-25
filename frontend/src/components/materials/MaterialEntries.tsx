import type { MaterialEntry } from "../../api/storyMaterials";
import { COVERAGE_LABEL, COVERAGE_TONE, FRESHNESS_LABEL, FRESHNESS_TONE } from "./labels";

function Badges({ entry }: { entry: MaterialEntry }) {
  return (
    <span className="flex flex-none items-center gap-2 text-xs">
      <span className={COVERAGE_TONE[entry.coverage]}>{COVERAGE_LABEL[entry.coverage]}</span>
      {entry.freshness !== "valid" && entry.freshness !== "none" && (
        <span className={FRESHNESS_TONE[entry.freshness]}>
          {FRESHNESS_LABEL[entry.freshness]}
        </span>
      )}
    </span>
  );
}

export function Row({
  entry,
  active,
  onOpen,
  extra,
}: {
  entry: MaterialEntry;
  active: boolean;
  onOpen: () => void;
  extra?: string;
}) {
  return (
    <li>
      <button
        className={`flex w-full items-start gap-3 px-3 py-2 text-left ${
          active ? "bg-sel-high" : "hover:bg-sel-high hover:text-fg"
        }`}
        onClick={onOpen}
      >
        <span className="min-w-0 flex-1">
          <span className="flex items-baseline gap-2">
            <span className="truncate text-sm font-bold">{entry.title}</span>
            {extra && <span className="flex-none text-xs text-sub">{extra}</span>}
          </span>
          <span className="mt-0.5 block truncate text-sm text-sub">
            {entry.subtitle || "—"}
          </span>
        </span>
        <Badges entry={entry} />
      </button>
    </li>
  );
}

export function CharactersList({
  entries,
  roster,
  selectedId,
  onOpen,
}: {
  entries: MaterialEntry[];
  roster: MaterialEntry[];
  selectedId?: string;
  onOpen: (entry: MaterialEntry) => void;
}) {
  const byEntity = new Map(roster.map((entry) => [entry.entity_path, entry]));
  if (entries.length === 0) {
    return (
      <p className="px-3 py-2 text-sm text-mute">
        还没有角色小传。在对话里建立小传后，这里会显示身份与状态。
      </p>
    );
  }
  return (
    <ul className="divide-y divide-row-edge">
      {entries.map((entry) => {
        const state = entry.entity_path ? byEntity.get(entry.entity_path) : undefined;
        const alive =
          state && state.domain === "roster"
            ? state.data.alive === null
              ? "生死未知"
              : state.data.alive
                ? "在世"
                : "已死亡"
            : "";
        return (
          <Row
            key={entry.id}
            entry={entry}
            active={entry.id === selectedId}
            onOpen={() => onOpen(entry)}
            extra={alive}
          />
        );
      })}
    </ul>
  );
}

export function CluesList({
  entries,
  selectedId,
  onOpen,
}: {
  entries: MaterialEntry[];
  selectedId?: string;
  onOpen: (entry: MaterialEntry) => void;
}) {
  const groups: { key: string; label: string; rows: MaterialEntry[] }[] = [
    { key: "open", label: "未回收", rows: [] },
    { key: "resolved", label: "已回收", rows: [] },
    { key: "dropped", label: "已废弃", rows: [] },
  ];
  for (const entry of entries) {
    if (entry.domain !== "clues") continue;
    const group = groups.find((item) => item.key === entry.data.status) ?? groups[0];
    group.rows.push(entry);
  }
  return (
    <div>
      {groups.map((group) => (
        <section key={group.key}>
          <h3 className="toc-head px-3 py-1 text-xs font-bold tracking-wide text-sub">
            {group.label}（{group.rows.length}）
          </h3>
          <ul className="divide-y divide-row-edge">
            {group.rows.map((entry) => (
              <Row
                key={entry.id}
                entry={entry}
                active={entry.id === selectedId}
                onOpen={() => onOpen(entry)}
                extra={
                  entry.domain === "clues" && entry.data.overdue ? "已超期" : undefined
                }
              />
            ))}
            {group.rows.length === 0 && (
              <li className="px-3 py-1 text-xs text-mute">没有。</li>
            )}
          </ul>
        </section>
      ))}
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return (
    <p className="text-sm leading-5">
      <span className="text-mute">{label}：</span>
      {value}
    </p>
  );
}

export function EntryDetail({
  entry,
  roster,
  onTakeToChat,
}: {
  entry: MaterialEntry;
  roster?: MaterialEntry;
  onTakeToChat: (path: string) => void;
}) {
  return (
    <div className="max-h-[55%] flex-none overflow-auto border-b border-panel-edge px-3 py-2 md:border-b-0">
      <h2 className="text-base font-bold">{entry.title}</h2>
      <p className="mt-0.5 text-xs text-mute">
        {entry.chapter !== null ? `第 ${entry.chapter} 章 · ` : ""}
        {COVERAGE_LABEL[entry.coverage]}
        {entry.freshness !== "valid" && entry.freshness !== "none"
          ? ` · ${FRESHNESS_LABEL[entry.freshness]}`
          : ""}
      </p>

      <div className="mt-2 space-y-1">
        {entry.domain === "chapter_index" && (
          <>
            <p className="whitespace-pre-wrap text-sm leading-5">
              {entry.data.summary || "还没有摘要。"}
            </p>
            {(
              [
                ["事件", entry.data.events],
                ["人物", entry.data.characters],
                ["时间地点", entry.data.places],
                ["线索", entry.data.clues],
              ] as const
            ).map(([label, rows]) =>
              rows.length ? (
                <div key={label}>
                  <h3 className="mt-1 text-xs font-bold text-sub">{label}</h3>
                  <ul className="list-disc pl-4 text-sm leading-5">
                    {rows.map((row, index) => (
                      <li key={index}>{row}</li>
                    ))}
                  </ul>
                </div>
              ) : null,
            )}
          </>
        )}

        {entry.domain === "timeline" && (
          <>
            <Field label="原文时间" value={entry.data.when || "时间未知"} />
            <Field label="跨度" value={entry.data.span} />
            <Field label="地点" value={entry.data.location} />
          </>
        )}

        {entry.domain === "clues" && (
          <>
            <Field label="状态" value={entry.data.label} />
            <Field label="埋设" value={`第 ${entry.data.planted_chapter} 章`} />
            <Field
              label="计划回收"
              value={entry.data.due_chapter ? `第 ${entry.data.due_chapter} 章` : "未定"}
            />
            <Field
              label="回收"
              value={
                entry.data.resolution_chapter ? `第 ${entry.data.resolution_chapter} 章` : ""
              }
            />
            <Field label="指向" value={entry.data.supports} />
            {entry.data.overdue && (
              <p className="text-xs text-amber">计划回收章已过，仍未回收。</p>
            )}
          </>
        )}

        {entry.domain === "glossary" && (
          <>
            <Field label="类别" value={entry.data.label} />
            <Field label="别名" value={entry.data.aliases.join("、")} />
            <Field
              label="首次出现"
              value={entry.data.first_chapter ? `第 ${entry.data.first_chapter} 章` : ""}
            />
            <Field
              label="来源"
              value={entry.data.source === "declared" ? "人物小传声明" : "正文登记"}
            />
          </>
        )}

        {entry.domain === "disclosure" && (
          <>
            <Field label="社会身份" value={entry.data.surface} />
            <Field label="别名" value={entry.data.aliases.join("、")} />
            <Field label="角色" value={entry.data.roles.join("、")} />
            <h3 className="mt-1 text-xs font-bold text-sub">读者已知</h3>
            {entry.data.revealed.length === 0 ? (
              <p className="text-sm text-mute">还没有已披露的信息。</p>
            ) : (
              <ul className="list-disc pl-4 text-sm leading-5">
                {entry.data.revealed.map((fact, index) => (
                  <li key={index}>
                    第 {fact.chapter} 章：{fact.fact}
                    <span className="ml-1 text-xs text-mute">
                      {fact.source === "text" ? "正文核对" : "作者声明"}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {entry.data.upcoming.length > 0 && (
              <>
                <h3 className="mt-1 text-xs font-bold text-sub">正文还没写到</h3>
                <ul className="list-disc pl-4 text-sm leading-5 text-mute">
                  {entry.data.upcoming.map((fact, index) => (
                    <li key={index}>
                      第 {fact.chapter} 章：{fact.fact}
                    </li>
                  ))}
                </ul>
              </>
            )}
            {entry.data.planned.length > 0 && (
              <>
                <h3 className="mt-1 text-xs font-bold text-sub">作者计划</h3>
                <ul className="list-disc pl-4 text-sm leading-5 text-mute">
                  {entry.data.planned.map((fact, index) => (
                    <li key={index}>
                      第 {fact.chapter} 章：{fact.fact}
                    </li>
                  ))}
                </ul>
                <p className="text-xs text-mute">计划不是正文事实，不进入写作上下文。</p>
              </>
            )}
            {roster && roster.domain === "roster" && (
              <>
                <h3 className="mt-1 text-xs font-bold text-sub">人物状态</h3>
                <Field
                  label="位置"
                  value={roster.data.location || (roster.data.chapter ? "位置未记" : "状态未知")}
                />
                <Field
                  label="生死"
                  value={
                    roster.data.alive === null ? "未知" : roster.data.alive ? "在世" : "已死亡"
                  }
                />
                <Field label="立场" value={roster.data.stance} />
                <Field
                  label="最近核对"
                  value={roster.data.chapter ? `第 ${roster.data.chapter} 章` : ""}
                />
                {roster.data.lag ? (
                  <p className="text-xs text-amber">
                    此后 {roster.data.lag} 章没有核对记录，这些章的变动未知。
                  </p>
                ) : null}
              </>
            )}
          </>
        )}

        {entry.domain === "roster" && (
          <>
            <Field
              label="位置"
              value={entry.data.location || (entry.data.chapter ? "位置未记" : "状态未知")}
            />
            <Field
              label="生死"
              value={
                entry.data.alive === null ? "未知" : entry.data.alive ? "在世" : "已死亡"
              }
            />
            <Field label="立场" value={entry.data.stance} />
            <Field
              label="最近核对"
              value={entry.data.chapter ? `第 ${entry.data.chapter} 章` : ""}
            />
            {entry.data.lag ? (
              <p className="text-xs text-amber">
                此后 {entry.data.lag} 章没有核对记录，这些章的变动未知。
              </p>
            ) : null}
          </>
        )}
      </div>

      {entry.entity_path && (
        <button
          className="btn btn-gray mt-2 px-2 py-0.5 text-xs"
          onClick={() => onTakeToChat(entry.entity_path as string)}
        >
          把人物小传带到对话
        </button>
      )}
    </div>
  );
}

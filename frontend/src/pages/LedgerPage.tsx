import { useEffect, useMemo, useState } from "react";
import { useV2Store } from "../state/v2";
import {
  Panel,
  Empty,
  Notice,
  Pill,
  titleOf,
  display,
  fieldLabels,
  kindLabels,
  type View,
} from "../components/Workbench";
const groups = [
  { key: "all", title: "全部故事对象", kinds: [] },
  {
    key: "people",
    title: "人物与关系",
    kinds: ["story.character", "story.relationship"],
  },
  {
    key: "mystery",
    title: "悬疑与伏笔",
    kinds: [
      "story.mystery_truth",
      "story.clue",
      "story.red_herring",
      "story.foreshadow",
    ],
  },
  {
    key: "timeline",
    title: "时间线与世界",
    kinds: [
      "story.timeline_event",
      "story.place",
      "story.world_rule",
      "story.premise",
    ],
  },
  {
    key: "facts",
    title: "三层事实",
    kinds: ["story.truth_fact", "story.knowledge", "story.disclosure"],
  },
  { key: "plan", title: "叙事计划", kinds: [] },
];
export default function LedgerPage({
  onNavigate,
}: {
  onNavigate(view: View): void;
}) {
  const artifacts = useV2Store((s) => s.storyArtifacts),
    focus = useV2Store((s) => s.focusArtifactId);
  const [group, setGroup] = useState("all"),
    [layer, setLayer] = useState("truth"),
    [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(focus);
  useEffect(() => {
    if (focus) {
      setSelected(focus);
      setGroup("all");
      setQuery("");
    }
  }, [focus]);
  const names = useMemo(
    () => Object.fromEntries(artifacts.map((a) => [a.artifact_id, titleOf(a)])),
    [artifacts],
  );
  const filtered = artifacts.filter((a) => {
    const category = groups.find((g) => g.key === group)!;
    if (group === "plan" && !a.kind.startsWith("narrative.")) return false;
    if (category.kinds.length && !category.kinds.includes(a.kind)) return false;
    if (
      group === "facts" &&
      a.kind !==
        (
          {
            truth: "story.truth_fact",
            knowledge: "story.knowledge",
            disclosure: "story.disclosure",
          } as Record<string, string>
        )[layer]
    )
      return false;
    return JSON.stringify(a.content)
      .toLocaleLowerCase()
      .includes(query.toLocaleLowerCase());
  });
  const current =
    artifacts.find((a) => a.artifact_id === selected) ?? filtered[0];
  return (
    <main className="page-grid" aria-label="故事账本">
      <aside className="space-y-4">
        <Panel title="故事资产" meta={`${artifacts.length} 项`}>
          {groups.map((g) => {
            const n = artifacts.filter(
              (a) =>
                g.key === "all" ||
                (g.key === "plan"
                  ? a.kind.startsWith("narrative.")
                  : g.kinds.includes(a.kind)),
            ).length;
            return (
              <button
                key={g.key}
                className={`asset-row w-full justify-between px-4 py-4 text-xs ${group === g.key ? "sel" : ""}`}
                onClick={() => {
                  setGroup(g.key);
                  setSelected(null);
                }}
              >
                <b>{g.title}</b>
                <span className="count-pill px-2">{n}</span>
              </button>
            );
          })}
        </Panel>
        <Notice>
          <strong>三层事实分别维护</strong>
          <br />
          真相描述世界中发生了什么；角色认知描述谁知道什么；读者披露描述读者在何时看见什么。
        </Notice>
      </aside>
      <section className="min-w-0 space-y-4">
        <Panel
          title="Canon · 故事事实与叙事计划"
          meta={`${filtered.length} 项`}
        >
          <div className="flex flex-wrap justify-between gap-3 border-b border-line p-4">
            {group === "facts" ? (
              <div className="seg text-xs">
                {[
                  { id: "truth", label: "真相" },
                  { id: "knowledge", label: "角色认知" },
                  { id: "disclosure", label: "读者披露" },
                ].map((l) => (
                  <button
                    className={`px-3 py-2 ${layer === l.id ? "on" : ""}`}
                    key={l.id}
                    onClick={() => {
                      setLayer(l.id);
                      setSelected(null);
                    }}
                  >
                    {l.label}
                  </button>
                ))}
              </div>
            ) : (
              <p className="text-xs text-sub">
                {groups.find((g) => g.key === group)?.title}
              </p>
            )}
            <input
              aria-label="筛选故事对象"
              className="well-input min-w-0 px-3 py-2 text-xs"
              placeholder="搜索当前分类…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <div>
            {filtered.map((a) => (
              <button
                key={a.artifact_id}
                className={`asset-row block w-full p-4 text-left ${current?.artifact_id === a.artifact_id ? "sel" : ""}`}
                onClick={() => setSelected(a.artifact_id)}
              >
                <span className="flex items-start justify-between gap-3">
                  <b className="text-sm text-fgStrong">{titleOf(a)}</b>
                  <Pill>{kindLabels[a.kind] ?? a.kind}</Pill>
                </span>
                <p className="mt-2 line-clamp-2 text-xs leading-6 text-sub">
                  {display(
                    a.content.description ||
                      a.content.identity ||
                      a.content.statement ||
                      a.content.purpose ||
                      a.content.goal,
                    names,
                  )}
                </p>
              </button>
            ))}
            {filtered.length === 0 && (
              <Empty>
                此分类没有匹配对象。你可以在创作台提出规划，接受后再回到这里查看。
              </Empty>
            )}
          </div>
        </Panel>
      </section>
      <aside className="space-y-4">
        <Panel
          title="对象详情"
          meta={current ? kindLabels[current.kind] : undefined}
        >
          {current ? (
            <div className="space-y-3 p-4">
              <h2 className="font-serif text-lg font-bold text-fgStrong">
                {titleOf(current)}
              </h2>
              <dl className="space-y-3 text-xs leading-6">
                {Object.entries(current.content)
                  .filter(
                    ([k]) => !["schema_version", "legacy_metadata"].includes(k),
                  )
                  .map(([key, value]) => (
                    <div key={key}>
                      <dt className="text-sub">{fieldLabels[key] ?? key}</dt>
                      <dd className="whitespace-pre-wrap break-words">
                        {display(value, names)}
                      </dd>
                    </div>
                  ))}
              </dl>
              <button
                className="btn mt-3 w-full py-2 text-xs"
                onClick={() => {
                  useV2Store.setState({ focusArtifactId: current.artifact_id });
                  onNavigate("changes");
                }}
              >
                从此对象发起变更 →
              </button>
            </div>
          ) : (
            <Empty>选择一个对象查看内容。</Empty>
          )}
        </Panel>
        <Notice>
          修改事实先进入变更集，保留原因、基础版本和下游影响；批准后再试改与审阅。
        </Notice>
      </aside>
    </main>
  );
}

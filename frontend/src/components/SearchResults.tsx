import { useV2Store } from "../state/v2";
import { kindLabels, titleOf } from "./Workbench";
export default function SearchResults({
  query,
  onNavigate,
  onClose,
}: {
  query: string;
  onNavigate(target: { kind: "chapter" | "artifact"; id: string }): void;
  onClose(): void;
}) {
  const chapters = useV2Store((s) => s.chapters),
    artifacts = useV2Store((s) => s.storyArtifacts);
  const q = query.toLocaleLowerCase();
  const results = [
    ...chapters
      .filter((c) => c.title.toLocaleLowerCase().includes(q))
      .map((c) => ({
        kind: "chapter" as const,
        id: c.chapter_id,
        title: c.title,
        label: "正文",
      })),
    ...artifacts
      .filter((a) => JSON.stringify(a.content).toLocaleLowerCase().includes(q))
      .map((a) => ({
        kind: "artifact" as const,
        id: a.artifact_id,
        title: titleOf(a),
        label: kindLabels[a.kind] ?? "故事对象",
      })),
  ].slice(0, 16);
  return (
    <div
      className="panel absolute right-0 top-10 z-40 max-h-96 w-80 overflow-auto shadow-surface"
      aria-label="搜索结果"
    >
      <div className="panel-head p-3 text-xs">
        搜索当前项目
        <button onClick={onClose} aria-label="关闭搜索">
          关闭
        </button>
      </div>
      {results.map((r) => (
        <button
          key={`${r.kind}:${r.id}`}
          className="asset-row w-full p-3 text-left text-xs hover:bg-blue-bg"
          onClick={() => onNavigate(r)}
        >
          <span className="text-blue">{r.label}</span>
          <span className="truncate">{r.title}</span>
        </button>
      ))}
      {results.length === 0 && (
        <p className="p-4 text-xs text-sub">没有匹配的章节或故事对象。</p>
      )}
    </div>
  );
}

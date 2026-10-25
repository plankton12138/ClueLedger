import { MATERIAL_SECTIONS, pageHash, type MaterialSection, type WorkspacePage } from "../../state/navigation";
export default function MaterialNavigation({ workspaceId, section, chapter }: { workspaceId: string; section: MaterialSection; chapter: number | null; onNavigate: (page: WorkspacePage) => void }) {
  return <nav className="materials-navigation module" aria-label="资料分类">{MATERIAL_SECTIONS.map(item => <a key={item.key} aria-current={item.key === section ? "page" : undefined}
    href={pageHash({ kind: "materials", workspaceId, section: item.key, chapter, order: "chapter" })}>{item.label}</a>)}</nav>;
}

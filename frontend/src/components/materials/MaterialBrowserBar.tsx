import type { StoryMaterials } from "../../api/storyMaterials";
import { MATERIAL_SECTIONS, type MaterialSection } from "../../state/navigation";
import { Button } from "../ui/Button";
export default function MaterialBrowserBar({ section, chapter, data, onChange, onReload }: { section: MaterialSection; chapter: number | null; data?: StoryMaterials; onChange: (chapter: number | null) => void; onReload: () => void }) {
  return <header className="materials-browser-bar"><h1>{MATERIAL_SECTIONS.find(item => item.key === section)?.label}</h1>
    <label>查看截至 <select className="ui-input" aria-label="查看截至第几章" value={chapter ?? ""} onChange={event => onChange(event.target.value === "" ? null : Number(event.target.value))}>
      <option value="">全书{data ? `（第 ${data.current_chapter} 章）` : ""}</option>{data?.chapters.map(item => <option key={item.path} value={item.chapter}>第 {item.chapter} 章</option>)}
    </select></label>
    {data && <span className="ui-field-hint">有结果 {data.coverage.found} · 无发现 {data.coverage.empty} · 信息不足 {data.coverage.insufficient} · 未整理 {data.coverage.unrecorded}{data.coverage.stale ? ` · 依据已改动 ${data.coverage.stale}` : ""}</span>}
    <Button size="compact" onClick={onReload}>重新读取资料</Button>
  </header>;
}

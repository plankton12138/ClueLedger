import { useModuleLibraryStore } from "../../state/moduleLibrary";
import { Button } from "../ui/Button";
export default function RevisionPicker({ head, onSelect }: { head: string | null | undefined; onSelect: (id: string) => void }) {
  const { revisions, selectedRevisionId, revisionsCursor, revisionsLoading, loadMoreRevisions } = useModuleLibraryStore();
  return <div className="revision-picker"><label>版本 <select className="ui-input" aria-label="模块版本" value={selectedRevisionId ?? ""} onChange={event => onSelect(event.target.value)}>
    {selectedRevisionId && !revisions.some(item => item.revision_id === selectedRevisionId) && <option value={selectedRevisionId}>已选版本</option>}
    {revisions.map(item => <option key={item.revision_id} value={item.revision_id}>{item.revision_id === head ? "最新" : new Date(item.created_at).toLocaleString("zh-CN", { hour12: false })}</option>)}
  </select></label>{revisionsCursor && <Button variant="quiet" size="compact" busy={revisionsLoading} onClick={() => void loadMoreRevisions()}>更多版本</Button>}</div>;
}

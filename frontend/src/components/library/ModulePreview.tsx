import type { RevisionDetail } from "../../api/moduleLibrary";
import ModuleContent from "./ModuleContent";
export default function ModulePreview({ detail, bodies }: { detail: RevisionDetail; bodies: Record<string, string> }) {
  return <ModuleContent document={{ detail,bodies }} />;
}

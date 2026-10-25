import type { MaterialDomain } from "../../api/storyMaterials";
import type { MaterialSection } from "../../state/navigation";
import { Button } from "../ui/Button";
export interface OrganizeChoice { domain: MaterialDomain; title: string; action: string; entitiesRequired: boolean }
export default function OrganizeActions({ section, hasChapters, hasEntities, onChoose }: { section: MaterialSection; hasChapters: boolean; hasEntities: boolean; onChoose: (choice: OrganizeChoice) => void }) {
  const actions: OrganizeChoice[] = section === "story" ? [{ domain: "story_index", title: "整理章节索引", action: "整理所选章节", entitiesRequired: false }]
    : section === "characters" ? [{ domain: "disclosure", title: "核对身份披露", action: "开始核对", entitiesRequired: true }, { domain: "roster", title: "整理人物状态", action: "开始整理", entitiesRequired: true }]
      : section === "clues" ? [{ domain: "clues", title: "整理线索", action: "从章节整理线索", entitiesRequired: false }]
        : [{ domain: "glossary", title: "整理名词", action: "从章节整理名词", entitiesRequired: false }];
  return <div className="organize-actions" role="group" aria-label="资料整理">{actions.map((item, index) => <Button key={item.domain} variant={index === 0 ? "primary" : "secondary"} size="compact" disabled={!hasChapters || (item.entitiesRequired && !hasEntities)}
    disabledReason={!hasChapters ? "作品还没有章节" : "先建立角色小传"} onClick={() => onChoose(item)}>{item.domain === "roster" ? "从章节整理人物状态" : section === "characters" ? item.title : item.action}</Button>)}</div>;
}

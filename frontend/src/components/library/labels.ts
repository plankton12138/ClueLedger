import type { ModuleKind } from "../../api/moduleLibrary";

export const KIND_LABEL: Record<ModuleKind, string> = {
  characters: "人物",
  world: "世界设定",
  clues: "线索与伏笔",
  truth: "真相与事件经过",
  timeline: "时间线",
  plot: "剧情安排",
  reference: "自由资料",
};

export const KIND_OPTIONS: ModuleKind[] = [
  "characters",
  "world",
  "clues",
  "truth",
  "timeline",
  "plot",
  "reference",
];

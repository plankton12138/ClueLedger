/**
 * 全书结构只读页的接口（v1.1 Q1 M2）。
 *
 * STRUCTURE.md 由模型自维护、作者只读；本文件只提供读取，不提供写入——调整结构
 * 在对话里说，模型改完入档（Git 可回退）。与后端 `story_api.py:get_structure` 对应。
 */

import { request } from "./workspace";

/** 结构文档固定位于作品根目录；与后端 `files.py:STRUCTURE_PATH` 保持一致。 */
export const STRUCTURE_PATH = "STRUCTURE.md";

export interface StructureView {
  exists: boolean;
  text: string;
  digest: string | null;
  size: number;
  within_budget: boolean;
  budget_limit: number;
}

export function readStructure(workspaceId: string): Promise<StructureView> {
  return request(`/${workspaceId}/story/structure`);
}

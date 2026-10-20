/**
 * 设定集读取客户端（V03-10 M3）：只保留正式条目的读取。
 *
 * V03-10 起设定的增删改都在对话里由模型提议、经对话内「采用」落盘，
 * 前端不再直接写入，故此处移除表单描述、创建/修订与候选确认系列方法，
 * 只留 `fetchLedger`（后端 `GET /ledger`，返回 kind 与 kind_label）。
 */

import { projectPrefix, request } from "./v2";

export interface LedgerEntry {
  id: string;
  kind: string;
  kind_label: string;
  title: string;
  summary: string;
}

export async function fetchLedger(projectId: string): Promise<LedgerEntry[]> {
  const payload = await request<{ entries: LedgerEntry[] }>(
    `${projectPrefix(projectId)}/ledger`,
  );
  return payload.entries;
}

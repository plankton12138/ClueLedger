/**
 * 工具执行（V05-2B/C）：一行摘要，默认折叠详情。
 *
 * 失败在折叠状态也看得见（红色标记 + 简短原因）；展开才显示定位参数与结果摘录。
 * 参数只来自服务端允许保留的那几项，整章正文与完整写入参数不会出现在这里。
 */

import { memo } from "react";

import { formatDuration, type ChatTool } from "../../state/chatMessages";

const TOOL_LABEL: Record<string, string> = {
  list_files: "列出目录",
  read_file: "读取",
  search_text: "搜索",
  write_file: "写入",
  edit_file: "修改",
  move_file: "移动",
  delete_file: "删除",
  git_diff: "查看差异",
  git_log: "查看历史",
  list_modules: "列出模块",
  query_records: "检索资料",
  read_record: "读取资料",
  search_module_library: "搜索模块库",
  read_story: "读取构思",
  update_story: "修改构思",
};

function toolLine(tool: ChatTool): string {
  const name = TOOL_LABEL[tool.name] ?? tool.name;
  const path = typeof tool.args.path === "string" ? tool.args.path : "";
  const source = typeof tool.args.source === "string" ? tool.args.source : "";
  const target = typeof tool.args.target === "string" ? tool.args.target : "";
  const query = typeof tool.args.query === "string" ? tool.args.query : "";
  const entry = typeof tool.args.entry_id === "string" ? tool.args.entry_id : "";
  if (source && target) return `${name} ${source} → ${target}`;
  if (path) return `${name} ${path}`;
  if (entry) return `${name} ${entry}`;
  if (query) return `${name}「${query}」`;
  return name;
}

interface Props {
  tool: ChatTool;
  submittedLater?: boolean;
}

function ToolActivity({ tool, submittedLater = false }: Props) {
  const running = tool.ok === null;
  const mark = running ? "·" : tool.ok ? "✓" : "✗";
  const duration = formatDuration(tool.durationMs);
  const detail: Array<[string, string]> = Object.entries(tool.args)
    .filter(([, value]) => typeof value === "string" || typeof value === "number")
    .map(([key, value]) => [key, String(value)]);

  return (
    <details className={`chat-tool ${tool.ok === false && !submittedLater ? "bad" : ""}`}>
      <summary className="chat-tool-line">
        <span className="chat-tool-mark">{mark}</span>
        <span className="chat-tool-name">{toolLine(tool)}</span>
        {running && <span className="chat-tool-note">进行中</span>}
        {duration && <span className="chat-tool-note">{duration}</span>}
        {submittedLater && <span className="chat-tool-note">提交前校验记录 · 后续已提交</span>}
        {tool.ok === false && !submittedLater && tool.summary && (
          <span className="chat-tool-fail">{tool.summary}</span>
        )}
      </summary>
      <div className="chat-tool-body">
        {detail.length > 0 && (
          <dl className="chat-tool-args">
            {detail.map(([key, value]) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        )}
        {tool.summary && <p className="chat-tool-result">{tool.summary}</p>}
      </div>
    </details>
  );
}

export default memo(ToolActivity);

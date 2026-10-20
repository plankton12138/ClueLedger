/**
 * 应用外壳（0.4 V04-4）：工作区是唯一主视图。
 *
 * 0.3 的写作台、作品选择器、模型设置与对话候选卡已从默认页面移除——它们依赖已退出
 * 的 `/api/v2`。这里只挂文件工作区壳层；旧组件仍在源码中，待 V04-5 清理。
 */

import ErrorBoundary from "./components/ErrorBoundary";
import WorkspaceShell from "./components/workspace/WorkspaceShell";

export default function App() {
  return (
    <ErrorBoundary label="工作区">
      <WorkspaceShell />
    </ErrorBoundary>
  );
}

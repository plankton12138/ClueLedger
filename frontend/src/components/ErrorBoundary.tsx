/**
 * 面板级错误边界（P6 T12，D10）：一个面板崩不拖垮整个应用。
 *
 * 用法：<ErrorBoundary label="正文">…</ErrorBoundary>——渲染期异常被捕获后
 * 显示错误卡片（面板名 / 错误摘要 / 一句建议），
 * 顶部导航与其他面板不受影响。「重试」重置边界状态，重新渲染子树。
 */

import { Component, type ErrorInfo, type ReactNode } from "react";

interface ErrorBoundaryProps {
  /** 面板名，显示在错误卡标题里。 */
  label: string;
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

export default class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 可观测性：边界兜底不静默——console 记组件栈，供开发者定位（P4 教训：未知不吞）。
    console.error(`[ErrorBoundary]「${this.props.label}」崩溃`, error, info.componentStack);
  }

  render(): ReactNode {
    if (this.state.error === null) {
      return this.props.children;
    }
    return (
      <div className="flex flex-1 items-center justify-center p-6">
        <div className="max-w-md rounded border border-red-edge bg-red-bg px-4 py-3">
          <p role="alert" className="text-sm text-red">[PANEL_RENDER_FAILED] {this.props.label}：面板暂时无法显示。建议：重试；持续失败时查看活动记录或刷新页面。</p>
          <button
            type="button"
            className="btn btn-gray mt-3 px-3 py-1 text-xs"
            onClick={() => this.setState({ error: null })}
          >
            重试
          </button>
        </div>
      </div>
    );
  }
}

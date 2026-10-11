/**
 * 面板级错误边界（P6 T12，D10）：一个面板崩不拖垮整个应用。
 *
 * 用法：<ErrorBoundary label="正文">…</ErrorBoundary>——渲染期异常被捕获后
 * 显示错误卡片（三要素式文案：哪个面板崩了 / 错误摘要 / 怎样算对），
 * 顶部导航与其他面板不受影响。「重试」重置边界状态，重新渲染子树。
 */

import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button } from "@/components/ui/button";

interface ErrorBoundaryProps {
  /** 面板名，进错误卡文案（「哪里错」的第一要素）。 */
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
        <div className="max-w-md border border-overdue/50 bg-overdue/10 px-4 py-3">
          <div className="text-sm font-bold text-overdue">「{this.props.label}」面板崩溃了</div>
          <p className="mt-1 text-xs leading-relaxed text-paper/75">
            哪里错：{this.state.error.message}
          </p>
          <p className="mt-2 text-xs leading-relaxed text-paper/50">
            怎样算对：点下方重试；若反复崩溃，检查服务端日志或刷新页面。
          </p>
          <Button
            variant="outline"
            onClick={() => this.setState({ error: null })}
            className="mt-3 h-auto border-gold/60 px-3 py-1 text-xs text-gold hover:bg-gold/15 hover:text-gold"
          >
            重试
          </Button>
        </div>
      </div>
    );
  }
}

import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import StartupGateView from "./StartupGateView";

function markup() {
  return renderToStaticMarkup(<StartupGateView onOpenChat={() => undefined} />);
}

describe("startup gate view", () => {
  it("explains the conversation-first start instead of a hand-written form", () => {
    const html = markup();
    expect(html).toContain("从一句话开始");
    expect(html).toContain("设定卡");
    expect(html).toContain("回到对话，开始起步");
    // 不提供绕过对话的手写入口（UC-D06：无隐藏路径）。
    expect(html).not.toContain("<textarea");
  });

  it("keeps the accessible label as 起步", () => {
    expect(markup()).toContain('aria-label="起步"');
  });
});

import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import type { StorySync } from "../../api/story";
import StorySyncStrip from "./StorySyncStrip";

const sync: StorySync = {
  variant_id: "main",
  revision: "rev",
  counts: {
    aligned: 4,
    outline_ahead: 1,
    prose_ahead: 2,
    diverged: 1,
    unchecked: 0,
    waived: 1,
    outline_only: 3,
    unbound_prose: 2,
  },
  nodes: [],
  chapters: [],
  conflicts: [],
};

describe("StorySyncStrip", () => {
  it("groups node states and exposes the unbound chapters", () => {
    const html = renderToStaticMarkup(<StorySyncStrip sync={sync} focus={null} onFocus={() => {}} />);
    expect(html).toContain("已对齐");
    expect(html).toContain(">4<");
    expect(html).toContain("待同步");
    expect(html).toContain(">3<");
    expect(html).toContain("冲突");
    expect(html).toContain(">1<");
    expect(html).toContain("未纳入构思");
    expect(html).toContain(">2<");
    expect(html).toContain("已确认差异");
  });

  it("marks the active metric", () => {
    const html = renderToStaticMarkup(<StorySyncStrip sync={sync} focus="conflict" onFocus={() => {}} />);
    expect(html).toContain('aria-pressed="true"');
  });
});

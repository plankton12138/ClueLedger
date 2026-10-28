import { expect, it } from "vitest";
import { codePointLength, selectionOffsets } from "./sourceSelection";

it("uses unicode code points for emoji and Chinese text", () => {
  const text = "前文🔑背面相同。";
  expect(codePointLength(text)).toBe(8);
  expect(selectionOffsets(text, "前文🔑", "背面相同。")).toEqual({ start: 3, end: 8, quote: "背面相同。" });
});

it("rejects a selection that does not match the source text", () => {
  expect(selectionOffsets("前文🔑背面相同。", "前文", "背面不同。")).toBeNull();
  expect(selectionOffsets("abc", "", "")).toBeNull();
});

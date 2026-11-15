import type { TraceMark, TraceMissingRow } from "../components/trace";

import type { CheckRow, EvidenceRef } from "./api";

const LENSES: Record<string, string> = {
  only_region_changed: "O",
  region_actually_changed: "O",
  preserved_verbatim: "O",
  committed_via_candidate: "I",
  chapter_in_model_input: "C",
  snow_present: "O",
};

export function checkLens(check: CheckRow): string {
  if (LENSES[check.id]) return LENSES[check.id];
  if (check.level === "human") return "O";
  if (check.level === "quality") return "C";
  return "I";
}

export function marksFromChecks(checks: CheckRow[]): TraceMark[] {
  const marks: TraceMark[] = [];
  for (const check of checks) {
    for (const ref of check.evidence || []) {
      if (ref.type === "span" && ref.span_id) {
        marks.push({
          span_id: ref.span_id,
          verdict: normalizeVerdict(check.verdict),
          check_id: check.id,
        });
      }
    }
  }
  return marks;
}

export function missingFromChecks(checks: CheckRow[]): TraceMissingRow[] {
  const rows: TraceMissingRow[] = [];
  for (const check of checks) {
    for (const ref of check.evidence || []) {
      if (ref.type === "missing") {
        const expected = ref.expected || "预期步骤";
        rows.push({
          id: `missing:${check.id}:${expected}`,
          name: expected,
          expected,
        });
      }
    }
  }
  return rows;
}

export function firstSpanRef(check: CheckRow): EvidenceRef | undefined {
  return (check.evidence || []).find(item => item.type === "span" && item.span_id);
}

export function hasFileRef(check: CheckRow): boolean {
  return (check.evidence || []).some(item => item.type === "file" || item.type === "note");
}

function normalizeVerdict(value: string): TraceMark["verdict"] {
  if (value === "pass" || value === "fail" || value === "skipped" || value === "pending_review") {
    return value;
  }
  return "skipped";
}

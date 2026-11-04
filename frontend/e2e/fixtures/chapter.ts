import { execFileSync } from "node:child_process";
import { resolve } from "node:path";

export interface SeededChapter {
  chapter_id: string;
  order: number;
  path: string;
  prose_digest: string;
  plan_revision: string;
}

export function seedChapter(
  book: { id: string; path: string },
  title: string,
  requestKey: string,
  expectedPlanRevision = "empty",
): SeededChapter {
  const dataDir = resolve(process.env.HASTINGS_E2E_DATA_DIR ?? "../data/test-tmp/e2e");
  const output = execFileSync(
    "uv",
    [
      "run", "--directory", "..", "python", resolve("e2e/fixtures/seed_chapter.py"),
      resolve(dataDir, "state"), book.id, book.path, title, requestKey, expectedPlanRevision,
    ],
    { windowsHide: true, encoding: "utf8", env: { ...process.env, PYTHONIOENCODING: "utf-8" } },
  );
  return JSON.parse(output.trim()) as SeededChapter;
}

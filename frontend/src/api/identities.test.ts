/**
 * V03-0.2 前后端身份契约的 TS 侧测试（计划 §4.1）。
 *
 * 与后端 tests/v2/manuscript/test_identities.py 读同一夹具
 * （./__fixtures__/v03-identities.json）：两侧都做 wire 往返逐字段比对，
 * 改动契约必须同时通过两侧测试。前端按 kind/code 分支，不解析中文消息。
 */

import { describe, expect, it } from "vitest";

import samples from "./__fixtures__/v03-identities.json";
import {
  ID_PREFIXES,
  isManuscriptKind,
  kindOfId,
  manuscriptRefToWire,
  operationEnvelopeToWire,
  parseManuscriptRef,
  parseOperationEnvelope,
  refMatchesKind,
  type ManuscriptKind,
} from "./identities";

describe("kindOfId / isManuscriptKind", () => {
  it("按 ID 前缀推断四类身份", () => {
    expect(kindOfId("art_9f2c")).toBe("formal");
    expect(kindOfId("rev_9f2c")).toBe("formal");
    expect(kindOfId("cmt_9f2c")).toBe("formal");
    expect(kindOfId("eds_9f2c")).toBe("edit_draft");
    expect(kindOfId("edc_9f2c")).toBe("edit_draft");
    expect(kindOfId("cnd_9f2c")).toBe("candidate");
    expect(kindOfId("tks_9f2c")).toBe("task_snapshot");
    expect(kindOfId("zzz_9f2c")).toBeNull();
    expect(kindOfId("noprefix")).toBeNull();
  });

  it("拒绝未知 kind 字面量", () => {
    expect(isManuscriptKind("formal")).toBe(true);
    expect(isManuscriptKind("beat")).toBe(false);
    expect(isManuscriptKind(42)).toBe(false);
  });
});

describe("refMatchesKind（I01：引用带类型）", () => {
  const formal = { kind: "formal" as ManuscriptKind, id: "art_1", commitId: "cmt_1" };
  it("同类同前缀通过", () => {
    expect(refMatchesKind(formal, "formal")).toBe(true);
  });
  it("跨身份拒绝（前缀或 kind 不匹配）", () => {
    expect(refMatchesKind(formal, "edit_draft")).toBe(false);
    expect(refMatchesKind({ kind: "formal", id: "cnd_1", commitId: "cmt_1" }, "formal")).toBe(false);
  });
  it("前缀表与后端 ID_PREFIXES 一致", () => {
    expect(ID_PREFIXES.edit_draft).toEqual(["eds", "edc"]);
    expect(ID_PREFIXES.candidate).toEqual(["cnd"]);
    expect(ID_PREFIXES.task_snapshot).toEqual(["tks"]);
  });
});

describe("parseManuscriptRef", () => {
  it("拒绝未知 kind", () => {
    expect(() => parseManuscriptRef({ kind: "beat", id: "art_1" })).toThrow(/kind/);
  });
  it("拒绝空白 ID", () => {
    expect(() => parseManuscriptRef({ kind: "formal", id: " art_1" })).toThrow(/id/);
  });
});

describe("前后端共用夹具往返（TS 侧）", () => {
  it("四条 ManuscriptRef 样例 wire 往返逐字段一致", () => {
    for (const wire of samples.manuscript_refs) {
      expect(manuscriptRefToWire(parseManuscriptRef(wire))).toEqual(wire);
    }
  });

  it("OperationEnvelope 样例 wire 往返逐字段一致", () => {
    for (const wire of samples.operation_envelopes) {
      expect(operationEnvelopeToWire(parseOperationEnvelope(wire))).toEqual(wire);
    }
  });

  it("no_change 信封不携带 commit_id（无差异不伪造 commit）", () => {
    const noChange = samples.operation_envelopes[1];
    expect(noChange.status).toBe("no_change");
    expect(noChange).not.toHaveProperty("commit_id");
  });

  it("错误信封样例带三要素与结构化 details", () => {
    const { error } = samples.error_envelope;
    expect(error.code).toBe("OPERATION_KEY_REUSED");
    expect(error.category).toBe("violation");
    expect(error.where).not.toBe("");
    expect(error.why).not.toBe("");
    expect(error.fix).not.toBe("");
    expect(error.details).toHaveProperty("operation_id");
  });
});

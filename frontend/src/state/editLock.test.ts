/**
 * 作者草稿锁的生命周期纯函数（P5 R3-①）：锁跟随 UI 编辑态，不跟随组件实例。
 * 「上一轮持有 → 本轮该释放谁 / 该持有谁」的全部情形都在这里钉住。
 */

import { describe, expect, it } from "vitest";
import { nextEditLock, type EditLockRef } from "./editLock";

const ch01: EditLockRef = { slug: "story-a", chapterId: "ch01" };

describe("nextEditLock", () => {
  it("进入编辑态：无旧锁 → 持有当前章，不释放", () => {
    expect(nextEditLock(null, { slug: "story-a", chapterId: "ch01", mode: "edit" })).toEqual({
      release: null,
      held: ch01,
    });
  });

  it("编辑态重复求值：同一 (slug, 章) 不重复释放", () => {
    expect(nextEditLock(ch01, { slug: "story-a", chapterId: "ch01", mode: "edit" })).toEqual({
      release: null,
      held: ch01,
    });
  });

  it("编辑态下切换章节：旧章锁必须释放，改持新章", () => {
    expect(nextEditLock(ch01, { slug: "story-a", chapterId: "ch02", mode: "edit" })).toEqual({
      release: ch01,
      held: { slug: "story-a", chapterId: "ch02" },
    });
  });

  it("编辑态下切换工作区（同章名不同案卷）：旧锁释放，改持新案卷的锁", () => {
    expect(nextEditLock(ch01, { slug: "story-b", chapterId: "ch01", mode: "edit" })).toEqual({
      release: ch01,
      held: { slug: "story-b", chapterId: "ch01" },
    });
  });

  it("退出编辑态（保存/放弃后回阅读或对账）：锁归还", () => {
    expect(nextEditLock(ch01, { slug: "story-a", chapterId: "ch01", mode: "diff" })).toEqual({
      release: ch01,
      held: null,
    });
  });

  it("工作区置空（slug=null）：锁归还，不残留", () => {
    expect(nextEditLock(ch01, { slug: null, chapterId: "ch01", mode: "edit" })).toEqual({
      release: ch01,
      held: null,
    });
  });

  it("未持锁时的一切迁移都是无害空操作", () => {
    expect(nextEditLock(null, { slug: null, chapterId: null, mode: "read" })).toEqual({
      release: null,
      held: null,
    });
  });
});
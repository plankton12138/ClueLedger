/**
 * 写作台的单章修改会话（V03-2.4 / M04）。
 *
 * 规则由状态机而不是 UI 记忆保证：
 * - 进入修改后，输入写入副本，不碰正式正文；
 * - 800ms 防抖自动保存，保存序号 CAS；冲突时停止自动保存并提示，不静默覆盖；
 * - 「完成」先 flush 再整批发布一次；「暂存返回」只离开视图，副本仍开着；
 * - 「放弃」结束副本，不发布、不删历史。
 *
 * 本阶段 UI 只开单章入口（计划 §6.4 的限制显式）：修改期间不切换章节。
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  abandonEditSession,
  currentEditSession,
  draftOf,
  finishEditSession,
  saveEditDraft,
  startEditSession,
  suspendEditSession,
  type EditSessionView,
  type FinishEditResult,
} from "../../api/editSessions";
import { message } from "../../state/v2";

const DEBOUNCE_MS = 800;

export interface EditSessionState {
  view: EditSessionView | null;
  active: boolean;
  text: string;
  seq: number;
  saving: boolean;
  error: string | null;
  conflict: boolean;
  start(): Promise<void>;
  resume(): void;
  setText(next: string): void;
  finish(): Promise<FinishEditResult | null>;
  suspend(): Promise<void>;
  abandon(): Promise<void>;
}

export function useEditSession({
  projectId,
  chapterId,
  formalText,
  onFinished,
}: {
  projectId: string | null;
  chapterId: string | null;
  formalText: string;
  onFinished(result: FinishEditResult): void;
}): EditSessionState {
  const [view, setView] = useState<EditSessionView | null>(null);
  const [active, setActive] = useState(false);
  const [text, setTextState] = useState("");
  const [seq, setSeq] = useState(0);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);

  const seqRef = useRef(0);
  const savedRef = useRef("");
  const pendingRef = useRef("");
  const savingRef = useRef(false);
  const timerRef = useRef<number | null>(null);
  const sessionRef = useRef<string | null>(null);
  const chapterRef = useRef<string | null>(null);

  useEffect(() => {
    sessionRef.current = view?.session?.session_id ?? null;
  }, [view]);
  useEffect(() => {
    chapterRef.current = chapterId;
  }, [chapterId]);

  // 换作品：载入该作品未结束的副本（只提示，不自动进入修改）。
  useEffect(() => {
    setActive(false);
    setView(null);
    if (!projectId) return;
    let alive = true;
    currentEditSession(projectId)
      .then((loaded) => {
        if (alive && loaded.session?.status === "open") setView(loaded);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [projectId]);

  const flush = useCallback(async (): Promise<boolean> => {
    const pid = projectId;
    const sid = sessionRef.current;
    const cid = chapterRef.current;
    if (!pid || !sid || !cid) return false;
    while (pendingRef.current !== savedRef.current) {
      if (savingRef.current) {
        await new Promise((resolve) => setTimeout(resolve, 40));
        continue;
      }
      const target = pendingRef.current;
      savingRef.current = true;
      setSaving(true);
      try {
        const draft = await saveEditDraft(pid, sid, cid, {
          expectedSaveSeq: seqRef.current,
          requestId: crypto.randomUUID(),
          text: target,
        });
        seqRef.current = draft.save_seq;
        savedRef.current = target;
        setSeq(draft.save_seq);
        setView((current) =>
          current
            ? {
                ...current,
                drafts: [
                  draft,
                  ...current.drafts.filter((item) => item.chapter_id !== cid),
                ],
              }
            : current,
        );
      } catch (e) {
        const code = (e as { code?: string }).code;
        if (code === "EDIT_SAVE_SEQ_CONFLICT") setConflict(true);
        setError(message(e));
        return false;
      } finally {
        savingRef.current = false;
        setSaving(false);
      }
    }
    return true;
  }, [projectId]);

  const activate = useCallback(
    (loaded: EditSessionView, cid: string, formal: string) => {
      const draft = draftOf(loaded, cid);
      const initial = draft?.text ?? formal;
      seqRef.current = draft?.save_seq ?? 0;
      savedRef.current = initial;
      pendingRef.current = initial;
      setSeq(seqRef.current);
      setTextState(initial);
      setConflict(false);
      setError(null);
      setActive(true);
    },
    [],
  );

  const start = useCallback(async () => {
    if (!projectId || !chapterId) return;
    try {
      const loaded = await startEditSession(projectId);
      setView(loaded);
      activate(loaded, chapterId, formalText);
    } catch (e) {
      setError(message(e));
    }
  }, [projectId, chapterId, formalText, activate]);

  const resume = useCallback(() => {
    if (!view || !chapterId) return;
    activate(view, chapterId, formalText);
  }, [view, chapterId, formalText, activate]);

  const setText = useCallback(
    (next: string) => {
      setTextState(next);
      pendingRef.current = next;
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      timerRef.current = window.setTimeout(() => {
        void flush();
      }, DEBOUNCE_MS);
    },
    [flush],
  );

  const finish = useCallback(async () => {
    if (!projectId || !view?.session || !chapterId) return null;
    const ok = await flush();
    if (!ok) return null;
    try {
      const result = await finishEditSession(projectId, view.session.session_id, {
        expectedSaveSequences: { [chapterId]: seqRef.current },
      });
      setActive(false);
      setView(null);
      setError(null);
      onFinished(result);
      return result;
    } catch (e) {
      setError(message(e));
      return null;
    }
  }, [projectId, view, chapterId, flush, onFinished]);

  const suspend = useCallback(async () => {
    if (!projectId || !view?.session) return;
    const ok = await flush();
    if (!ok) return; // 有未保存内容或序号冲突：留在修改视图，避免「暂存」变成丢弃
    try {
      const loaded = await suspendEditSession(projectId, view.session.session_id);
      setView(loaded);
      setActive(false);
    } catch (e) {
      setError(message(e));
    }
  }, [projectId, view, flush]);

  const abandon = useCallback(async () => {
    if (!projectId || !view?.session) return;
    try {
      await abandonEditSession(projectId, view.session.session_id);
      setView(null);
      setActive(false);
      setError(null);
      setConflict(false);
    } catch (e) {
      setError(message(e));
    }
  }, [projectId, view]);

  return {
    view,
    active,
    text,
    seq,
    saving,
    error,
    conflict,
    start,
    resume,
    setText,
    finish,
    suspend,
    abandon,
  };
}

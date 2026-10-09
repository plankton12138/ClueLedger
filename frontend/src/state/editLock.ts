/**
 * 作者草稿锁的前端生命周期（P5 R3-①）：锁必须跟随「UI 编辑态」，而不是组件
 * 实例或某个按钮的点击——否则切章节/切工作区/切到事件流都会把服务端的
 * EditMark 留在原章，agent 的 write_chapter 对该章被长期拒绝。
 *
 * 纯函数：给出「上一轮持有的锁」与「当前 (slug, chapterId, mode)」，返回本轮
 * 应释放的锁与下一轮持有的锁；组件只把结果翻成 releaseEditMark 调用（IO 留在
 * 组件，与 store「只存状态」的分工一致）。
 */

export interface EditLockRef {
  slug: string;
  chapterId: string;
}

export interface EditLockStep {
  /** 需要释放的旧锁；无则 null。 */
  release: EditLockRef | null;
  /** 下一轮持有的锁（编辑态且已定位到某章则为该章）。 */
  held: EditLockRef | null;
}

export function nextEditLock(
  held: EditLockRef | null,
  current: { slug: string | null; chapterId: string | null; mode: string },
): EditLockStep {
  const { slug, chapterId, mode } = current;
  if (slug !== null && chapterId !== null && mode === "edit") {
    const lock: EditLockRef = { slug, chapterId };
    const same = held !== null && held.slug === lock.slug && held.chapterId === lock.chapterId;
    return { release: same ? null : held, held: lock };
  }
  // 非编辑态（阅读/对账/未选章/切换工作区）：持有的锁一律归还
  return { release: held, held: null };
}
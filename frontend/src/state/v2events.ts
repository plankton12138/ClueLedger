export function advanceCursor(
  cursor: number,
  frame: Record<string, unknown>,
): { cursor: number; refresh: boolean } {
  const seq =
    frame.type === "event"
      ? frame.sequence
      : frame.type === "replayed"
        ? frame.up_to_seq
        : null;
  if (typeof seq !== "number" || !Number.isSafeInteger(seq) || seq < 0)
    return { cursor, refresh: false };
  return { cursor: Math.max(cursor, seq), refresh: seq > cursor };
}

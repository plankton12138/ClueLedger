import { useEffect } from "react";
import { useModelStore } from "../state/models";

export default function ModelButton({ onClick }: { onClick(): void }) {
  const current = useModelStore((s) => s.current);
  const error = useModelStore((s) => s.error);
  useEffect(() => {
    void useModelStore.getState().refresh();
  }, []);
  return (
    <button
      type="button"
      aria-label="切换模型"
      onClick={onClick}
      title={
        error ||
        `${current?.display_name || "模型"} · ${current?.model || "尚未配置"}`
      }
      className="project-picker hidden max-w-40 truncate px-3 py-2 text-[11px] sm:block"
    >
      {error ? "模型读取失败" : current?.available ? current.model : "连接模型"}{" "}
      ▾
    </button>
  );
}

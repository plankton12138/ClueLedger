import { useState } from "react";
import { Sheet, SheetContent, SheetTitle, SheetDescription } from "./ui/sheet";
import { createProject, fetchManuscript } from "../api/v2";
import ModelSettings from "./ModelSettings";
import { registerProject, useV2Store, message, isDirty } from "../state/v2";
import { ErrorText, Panel } from "./Workbench";

export default function SettingsDrawer({
  open,
  onOpenChange,
  modelsOnly = false,
}: {
  open: boolean;
  onOpenChange(open: boolean): void;
  modelsOnly?: boolean;
}) {
  const [name, setName] = useState(""),
    [existing, setExisting] = useState("");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const projects = useV2Store((s) => s.projects);
  const perform = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };
  function canSwitch() {
    const s = useV2Store.getState();
    if (isDirty(s.chapter) || s.chapter.saving)
      throw new Error(
        "当前正文有未保存修改或正在保存，请先回创作台处理，再切换项目。",
      );
  }
  function select(id: string) {
    useV2Store.getState().selectProject(id);
    onOpenChange(false);
  }
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-[520px] max-w-[95vw] overflow-y-auto p-5">
        <div className="flex items-center justify-between">
          <SheetTitle className="font-serif text-xl">
            {modelsOnly ? "模型设置" : "设置"}
          </SheetTitle>
          <button
            className="btn btn-gray px-3 py-1"
            onClick={() => onOpenChange(false)}
          >
            关闭
          </button>
        </div>
        <SheetDescription className="mt-2 text-xs text-sub">
          管理项目与接入模型。
        </SheetDescription>
        <div className="mt-5 space-y-4">
          <ErrorText>{error}</ErrorText>
          {!modelsOnly && (
            <Panel title="项目管理">
              <form
                className="space-y-3 p-4"
                onSubmit={(e) => {
                  e.preventDefault();
                  void perform(async () => {
                    canSwitch();
                    const p = await createProject(name.trim());
                    registerProject({
                      projectId: p.project_id,
                      name: p.name,
                      source: "created",
                    });
                    select(p.project_id);
                  });
                }}
              >
                <label className="field">
                  故事名称
                  <input
                    aria-label="新故事名称"
                    required
                    className="well-input"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="为新作品起一个名字"
                  />
                </label>
                <button
                  className="btn px-4 py-2"
                  disabled={busy || !name.trim()}
                >
                  新建故事
                </button>
              </form>
              <div className="border-t border-line p-4">
                <p className="mb-2 text-xs text-sub">
                  本浏览器已登记 {projects.length}{" "}
                  个项目。也可用已有项目编号找回。
                </p>
                <form
                  className="flex gap-2"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void perform(async () => {
                      canSwitch();
                      await fetchManuscript(existing.trim());
                      registerProject({
                        projectId: existing.trim(),
                        name: existing.trim(),
                        source: "created",
                      });
                      select(existing.trim());
                    });
                  }}
                >
                  <input
                    aria-label="已有项目编号"
                    className="well-input min-w-0 flex-1 px-3 py-2 text-xs"
                    value={existing}
                    onChange={(e) => setExisting(e.target.value)}
                    required
                  />
                  <button className="btn btn-gray px-3 text-xs" disabled={busy}>
                    载入项目
                  </button>
                </form>
              </div>
            </Panel>
          )}
          <Panel title="模型接入">
            <ModelSettings />
          </Panel>
        </div>
      </SheetContent>
    </Sheet>
  );
}

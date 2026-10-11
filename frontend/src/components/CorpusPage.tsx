/**
 * 语料库页（P7 T7，计划 D6）：作者级文风语料管理 + 确定性文风档案。
 *
 * - 左列：上传区（拖拽 + 点选，multipart POST /api/corpus）+ 文件列表
 *   （文件名即主键；删除带确认，409/422 的三要素 detail 原样透出）；
 * - 右列：文风档案——句长六桶 Histogram（T2 共享组件）+ 对话占比 RingStat
 *   + 平均句长/段落平均/总字数指标卡（确定性统计，零 LLM）；
 * - 置灰区：「核心文风标签」「线索密度」「叙事节奏」标注「语义指标 · P8/P9
 *   接入」；向量索引进度区置灰「P8 RAG 接入」（合法空态，不报错不装死）。
 * - 泄压阀：单文件读码失败不 500（warnings 三要素逐条展示，合法但部分
 *   不可读）；上传/删除失败消息就地展示（服务端已给三要素）。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  deleteCorpus,
  fetchCorpus,
  fetchStyleProfile,
  uploadCorpus,
  type CorpusFileInfoWire,
  type StyleProfileWire,
} from "../api/rest";
import { Button } from "@/components/ui/button";
import Histogram, { type HistogramBucket } from "./Histogram";
import RingStat from "./RingStat";

/** 句长六桶固定顺序（服务端 dict 顺序不作为契约，前端定序渲染）。 */
const BUCKET_ORDER = ["1-10", "11-20", "21-30", "31-40", "41-50", "51+"] as const;

/** v1 允许的语料后缀（与服务端 ALLOWED_SUFFIXES 同口径）。 */
const ALLOWED_SUFFIXES = [".txt", ".md"] as const;

/** 字节数 → 人类可读（KB 进位 1024；≥1MB 显示一位小数）。 */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** 服务端 buckets Record → 固定顺序直方图桶序列（缺失桶补 0）。 */
export function bucketsOf(
  buckets: Record<string, number> | undefined,
): HistogramBucket[] {
  return BUCKET_ORDER.map((label) => ({ label, count: buckets?.[label] ?? 0 }));
}

function isAllowedName(name: string): boolean {
  const lower = name.toLowerCase();
  return ALLOWED_SUFFIXES.some((s) => lower.endsWith(s));
}

export default function CorpusPage() {
  const [files, setFiles] = useState<CorpusFileInfoWire[] | null>(null);
  const [profile, setProfile] = useState<StyleProfileWire | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      const [list, prof] = await Promise.all([fetchCorpus(), fetchStyleProfile()]);
      setFiles(list);
      setProfile(prof);
      setLoadError(null);
    } catch (exc) {
      setLoadError(exc instanceof Error ? exc.message : String(exc));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const uploadAll = useCallback(
    async (picked: FileList | File[]) => {
      const candidates = Array.from(picked).filter((f) => isAllowedName(f.name));
      const rejected = Array.from(picked).length - candidates.length;
      if (candidates.length === 0) {
        setMessage(rejected > 0 ? "已跳过：v1 仅支持 .txt / .md 文件。" : "没有可选文件。");
        return;
      }
      setBusy(true);
      setMessage(null);
      const errors: string[] = [];
      for (const file of candidates) {
        try {
          await uploadCorpus(file);
        } catch (exc) {
          errors.push(exc instanceof Error ? exc.message : String(exc));
        }
      }
      setBusy(false);
      if (errors.length > 0) {
        setMessage(errors.join("\n"));
      } else {
        setMessage(`已上传 ${candidates.length} 个文件。`);
      }
      await refresh();
    },
    [refresh],
  );

  const remove = useCallback(
    async (name: string) => {
      if (!window.confirm(`确认删除语料「${name}」？此操作不可恢复。`)) return;
      try {
        await deleteCorpus(name);
        await refresh();
      } catch (exc) {
        setMessage(exc instanceof Error ? exc.message : String(exc));
      }
    },
    [refresh],
  );

  const hasCorpus = (profile?.total_chars ?? 0) > 0;

  return (
    <main className="flex min-h-0 flex-1 flex-col border-t border-line">
      <div className="flex flex-none items-center gap-3 border-b border-line bg-ink-soft/50 px-4 py-2">
        <h2 className="font-serif text-sm font-bold text-gold">语料库</h2>
        <span className="text-xs text-paper/45">作者级文风语料（跨文章共享）</span>
      </div>
      <div className="flex min-h-0 flex-1 flex-col overflow-auto lg:flex-row lg:overflow-hidden">
        {/* 左列：上传区 + 文件列表。 */}
        <section className="flex w-full flex-none flex-col border-b border-line px-4 py-3 lg:w-96 lg:border-b-0 lg:border-r">
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragOver(false);
              void uploadAll(e.dataTransfer.files);
            }}
            className={`flex flex-col items-center gap-1 border border-dashed px-4 py-6 text-center transition-colors ${
              dragOver ? "border-gold bg-gold/10" : "border-line"
            }`}
          >
            <p className="text-xs text-paper/70">拖拽 .txt / .md 文件到此处</p>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => inputRef.current?.click()}
              className="h-auto px-2 py-0.5 text-xs text-gold-soft hover:border-gold/60 hover:bg-transparent hover:text-gold"
            >
              {busy ? "上传中…" : "或点选文件上传"}
            </Button>
            <input
              ref={inputRef}
              type="file"
              accept=".txt,.md"
              multiple
              className="hidden"
              onChange={(e) => {
                if (e.target.files !== null) void uploadAll(e.target.files);
                e.target.value = ""; // 同名文件重选也能触发 onChange
              }}
            />
          </div>
          {message !== null && (
            <p className="mt-2 whitespace-pre-wrap border border-line bg-ink-soft/60 px-2 py-1.5 text-xs leading-relaxed text-paper/80">
              {message}
            </p>
          )}
          <h3 className="mt-4 mb-2 font-serif text-sm font-bold text-gold">语料文件</h3>
          {loadError !== null && (
            <p className="border border-overdue/50 bg-overdue/10 px-2 py-1.5 text-xs leading-relaxed text-paper">
              {loadError}
            </p>
          )}
          {files !== null && files.length === 0 && (
            <p className="text-xs text-paper/40">
              暂无语料。上传你过去的写作文本，文风档案据此统计（对账功能据此习得你的行文习惯）。
            </p>
          )}
          <ul className="min-h-0 flex-1 space-y-1.5 overflow-auto text-xs">
            {(files ?? []).map((f) => (
              <li
                key={f.name}
                className="flex min-w-0 items-center gap-2 border border-line/60 bg-ink-soft/40 px-2 py-1.5"
              >
                <span className="min-w-0 flex-1 truncate text-paper/85" title={f.name}>
                  {f.name}
                </span>
                <span className="whitespace-nowrap font-mono text-[10px] text-paper/45">
                  {formatBytes(f.size_bytes)} · {f.uploaded_at.slice(0, 10)}
                </span>
                <Button
                  variant="outline"
                  onClick={() => void remove(f.name)}
                  className="h-auto shrink-0 px-1.5 py-px text-[10px] text-paper/60 hover:border-overdue/60 hover:bg-transparent hover:text-overdue"
                  title="删除语料"
                >
                  删除
                </Button>
              </li>
            ))}
          </ul>
          {profile !== null && profile.warnings.length > 0 && (
            <div className="mt-3 border border-gold/40 bg-gold/5 px-2 py-1.5">
              <p className="mb-1 text-[10px] text-gold">部分语料不可读（已跳过）</p>
              <ul className="space-y-1">
                {profile.warnings.map((w, i) => (
                  <li key={i} className="text-[10px] leading-relaxed text-paper/60">
                    {w}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
        {/* 右列：文风档案（确定性统计）。 */}
        <section className="flex min-h-0 min-w-0 flex-1 flex-col gap-4 overflow-auto px-4 py-3">
          <div>
            <h3 className="mb-2 font-serif text-sm font-bold text-gold">文风档案</h3>
            {profile === null ? (
              <p className="text-xs text-paper/40">{loadError === null ? "档案读取中…" : "—"}</p>
            ) : !hasCorpus ? (
              <p className="text-xs text-paper/40">暂无语料——上传后自动生成文风档案。</p>
            ) : (
              <>
                <div className="mb-3 flex flex-wrap items-center gap-4">
                  <div className="flex items-center gap-2">
                    <RingStat
                      rate={profile.dialogue_ratio}
                      size={56}
                      label="对话占比"
                      color="text-planned"
                    />
                    <div className="text-xs leading-relaxed">
                      <div className="text-paper/70">对话占比</div>
                      <div className="text-paper/45">引号内字符 / 总字符</div>
                    </div>
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    <MetricCard label="平均句长" value={`${profile.avg_sentence_length.toFixed(1)} 字`} />
                    <MetricCard label="段落平均" value={`${profile.avg_paragraph_length.toFixed(1)} 字`} />
                    <MetricCard label="总字数" value={profile.total_chars.toLocaleString("zh-CN")} />
                  </div>
                </div>
                <p className="mb-1 text-xs text-paper/50">
                  句长分布（共 {profile.sentence_count} 句，hover 查各桶计数）
                </p>
                <Histogram
                  buckets={bucketsOf(profile.sentence_buckets)}
                  ariaLabel="句长六桶分布直方图"
                />
              </>
            )}
          </div>
          {/* 置灰区（D6）：语义指标与向量索引在 P8/P9 接入，先立空态占位。 */}
          <div className="border-t border-line pt-3 opacity-50">
            <h3 className="mb-2 font-serif text-sm font-bold text-gold/60">核心文风标签</h3>
            <div className="flex flex-wrap gap-2">
              {["叙事视角", "比喻密度", "冷开场", "短句节奏"].map((tag) => (
                <span key={tag} className="border border-line px-2 py-0.5 text-xs text-paper/40">
                  {tag}
                </span>
              ))}
            </div>
            <p className="mt-1.5 text-[10px] text-paper/35">语义指标 · P8/P9 接入</p>
          </div>
          <div className="border-t border-line pt-3 opacity-50">
            <h3 className="mb-2 font-serif text-sm font-bold text-gold/60">向量索引进度</h3>
            <p className="text-xs text-paper/40">语料分块嵌入与检索就绪度 · P8 RAG 接入</p>
          </div>
        </section>
      </div>
    </main>
  );
}

/** 指标卡：标签 + 等宽值（与事件流概览卡同款样式，压缩时 truncate 兜底）。 */
function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-24 border border-line bg-ink-soft/60 px-2 py-1.5">
      <div className="truncate whitespace-nowrap text-[10px] text-paper/40">{label}</div>
      <div className="truncate whitespace-nowrap font-mono text-sm text-paper">{value}</div>
    </div>
  );
}

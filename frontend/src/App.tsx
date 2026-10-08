// P3 T6 骨架页：只验证设计语言与工具链接线。
// 对话流（T8）、故事账本与调试视图（T9）在此页面上逐步长出。
export default function App() {
  return (
    <main className="min-h-screen bg-ink">
      <header className="border-b border-line px-8 py-6">
        <h1 className="font-serif text-3xl font-bold text-gold">黑斯廷斯备忘录</h1>
        <p className="mt-1 text-sm text-gold-soft">Hastings&rsquo; Memo · 会记账的悬疑小说 AI 主编</p>
      </header>
      <section className="px-8 py-20 text-center">
        <p className="font-serif text-xl text-paper">伏笔必回收，线索必公平。</p>
        <p className="mt-4 text-sm text-paper/60">
          前端骨架已就绪（P3 T6）——对话流与故事账本将在 T8 / T9 接入。
        </p>
      </section>
    </main>
  );
}

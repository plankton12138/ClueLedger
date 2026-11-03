/**
 * 起步门（v1.1 M1）。
 *
 * 空作品（还没有作品意图）不再给手写构思表单：按产品规则，起步在对话里完成——
 * 引导提问或自由发挥产出设定卡，确认后模型自动起草全书结构与首批模块。
 * 已有意图的作品走「全书构思」页（v1.0.9 现状，UI 收敛顺延）。
 */

import { MessagesSquare, Sparkles } from "lucide-react";

import { Button } from "../ui/Button";

export default function StartupGateView({ onOpenChat }: { onOpenChat: () => void }) {
  return <article className="plan-view" aria-label="起步">
    <header className="plan-view-header">
      <h1><Sparkles size={20} strokeWidth={1.8} aria-hidden="true" />从一句话开始</h1>
      <p>这个作品还没有开始。回到对话，把你的想法说给模型：它可以先问你几个问题（随时可以说「就这样开始」），也可以直接接你的一句话自由发挥。</p>
    </header>
    <section className="plan-section" aria-label="起步会发生什么">
      <h2>接下来会发生什么</h2>
      <ul className="plan-thread-list">
        <li>对话整理出一张设定卡（作品意图），你确认后才写入作品。</li>
        <li>确认后模型起草第一版全书结构（铁律、路线、骨架、模块索引）并创建首批模块。</li>
        <li>结构和模块都直接入档、可回退；不满意就在对话里说一句改回来。</li>
      </ul>
    </section>
    <div className="plan-field">
      <Button size="compact" variant="primary" onClick={onOpenChat}>
        <MessagesSquare size={15} strokeWidth={2} aria-hidden="true" />回到对话，开始起步
      </Button>
    </div>
  </article>;
}

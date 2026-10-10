# B4：确认后推翻设定（回退边走查，接 A 组 slug）

**测试点**：bible_confirmed 阶段作者推翻核心设定，模型应走 `story_stage rollback`（bible_confirmed→bible_drafting）而非原地乱改。

**前置**：先跑完 [A-lifecycle.md](A-lifecycle.md) 轮次 1-4 到 bible_confirmed，沿用同一 slug。

**输入**：

```text
等等，我重新想了下——凶手不要是那位假医生了，换成另一位客人。你从已有嫌疑人里给 2-3 个候选并说明各自的动机与手法可行性，我确认后再重改人物关系和手法。
```

**观察**：

- [ ] 调用 `[tool] story_stage action=rollback`，reason 写明「作者推翻核心设定」
- [ ] 回到 bible_drafting 后按建账顺序重改受影响账本（手法/人物/章纲），未受影响的账本不盲目重写
- [ ] 重新请求三类点确认后才再次 advance

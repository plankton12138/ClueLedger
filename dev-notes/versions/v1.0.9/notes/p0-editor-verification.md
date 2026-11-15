# 编辑器内核验证方案（P0 登记）

日期：2026-11-02。状态：**方案已登记，尚未执行**。执行时点：P2 正文编辑器开工前。结果写入 `notes/editor-verification.md`，并据此冻结编辑内核；在此之前不得声称正文编辑可靠。

## 1. 要回答的问题

1. 原生 `<textarea>` 加适配层，能否支撑真实中文长章的输入、撤销与保存？
2. 若不能，是否引入专用编辑器（CodeMirror 6 一类），以及引入后中文输入法、选区与撤销是否仍然可靠？
3. 无论选哪种内核，页面只依赖适配接口，后续替换不触碰业务状态。

## 2. 候选

| 方案 | 组成 | 优点 | 风险 |
|---|---|---|---|
| A. 原生 textarea | `<textarea>` + 独立 Markdown 预览 + 适配层 | 无新依赖、输入法行为由浏览器负责、实现简单 | 长文滚动、行内高亮、双向定位需要额外计算 |
| B. 专用编辑器 | CodeMirror 6 或同类 + 适配层 | 行级渲染、选区与滚动定位成熟 | 依赖体积、中文 composition、撤销栈与受控状态冲突 |

P0 不预选方案；P2 用同一组场景比较后决定。

## 3. 验证样本

- 8,000—12,000 个中文字符，120 段以上，包含标题、空行、列表和引号。
- 至少 3 个 emoji 与非 BMP 汉字；至少 1 处同段重复引文，用于定位测试。
- 一段 1,500 字不换行的长段落，用于观察滚动与输入延迟。
- 两个章节文件，用于切章和返回位置测试。

样本放在 `data/test-tmp/editor/` 下的隔离作品，不写入作者作品目录。

## 4. 场景与通过条件

| 编号 | 场景 | 通过条件 |
|---|---|---|
| E01 | 连续输入 300 字中文 | 无丢字、无重复字符；光标不跳 |
| E02 | 拼音输入法合成（含选词、回退、长句） | `compositionstart` 到 `compositionend` 之间不触发保存；合成结束后保存内容与屏幕一致 |
| E03 | 浏览器撤销 / 重做 20 次 | 文本、光标与选区恢复；撤销栈不被异步保存清空 |
| E04 | 粘贴 5,000 字 | 不卡死；粘贴后摘要与保存结果一致 |
| E05 | 选中 200 字替换 | 只改选区；保存后摘要变化一次 |
| E06 | 自动保存合并 | 连续输入只发一次正文保存；失败时保留本地文字与撤销栈 |
| E07 | 两窗口同时保存 | 后到者保留本地草稿并显示摘要冲突，不用服务端覆盖本地 |
| E08 | 切章、前进、后退、刷新 | 未保存草稿有保护；返回后光标与滚动位置按规则恢复 |
| E09 | 1440 / 1024 / 760 / 640 / 390 宽度 | 只有一个主要写作面；编辑器不产生页面级水平滚动 |
| E10 | 100% / 150% / 200% 缩放 | 光标、选区、错误提示和主操作可达 |
| E11 | 减少动态效果 | 保存与索引状态仍有静态文字，不依赖动画 |
| E12 | 键盘 | Tab 顺序、焦点环、Escape 行为一致；纯图标按钮有名称 |

延迟只记录，不预设承诺：记录每次按键到下一次绘制的 p95、长段落滚动帧率、切章耗时；出现明显卡顿即视为方案不通过，换内核后复测同一场景。

E02、E03、E08 无法由脚本完全代替，必须在 Chromium 中手动执行并记录输入法名称与版本；自动化只用于回归已知行为。

## 5. 适配接口（页面唯一依赖）

```typescript
interface ProseEditorHandle {
  getValue(): string;
  setValue(text: string, options?: { preserveSelection?: boolean }): void;
  getSelection(): { start: number; end: number; quote: string } | null;
  replaceRange(start: number, end: number, text: string): void;
  insertAtCursor(text: string): void;
  focus(): void;
  scrollToRange(start: number, end: number): void;
  undo(): void;
  redo(): void;
  isComposing(): boolean;
}

interface ProseEditorProps {
  initialValue: string;
  readOnly: boolean;
  onChange(text: string, selection: { start: number; end: number }): void;
  onCompositionChange(active: boolean): void;
  onSelectionChange(selection: { start: number; end: number; quote: string } | null): void;
  onSaveRequest(): void;
}
```

- 业务层不直接访问 DOM 或编辑器实例；页边索引定位、候选插入和冲突对比都通过该接口。
- `onChange` 只报告文本与选区，不决定何时保存；自动保存合并由写作状态层负责。
- 只读状态必须由接口强制，而不是仅靠 CSS 遮罩。

## 6. 执行与记录

1. 用 P1/P2 的真实作品接口准备样本，不写前端假数据。
2. 方案 A 与方案 B 各实现同一适配接口的最小版本，只挂在测试页面。
3. 按 E01—E12 顺序执行；失败项记录复现步骤、浏览器版本、输入法与截图。
4. 记录选择结论、依赖体积、未覆盖场景和回退方式，写入 `notes/editor-verification.md`。
5. 结论只影响内核选择，不改变第 3 节的保存契约。

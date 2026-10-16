# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: v2-workbench.spec.ts >> 七页导航、空状态、新建项目与响应式外壳
- Location: e2e\v2-workbench.spec.ts:50:1

# Error details

```
Test timeout of 30000ms exceeded.
```

# Page snapshot

```yaml
- generic [ref=e3]:
  - banner [ref=e4]:
    - generic [ref=e5]: H
    - combobox "选择项目" [ref=e6] [cursor=pointer]:
      - generic: 雾港来信
    - status [ref=e9]: 已连接
    - button "隐藏对话栏" [pressed] [ref=e11] [cursor=pointer]
    - button "设置" [ref=e14] [cursor=pointer]
  - navigation "主导航" [ref=e18]:
    - button "项目概览" [active] [ref=e19] [cursor=pointer]
    - button "创作台" [ref=e25] [cursor=pointer]
    - button "故事账本" [ref=e29] [cursor=pointer]
    - button "变更影响" [ref=e32] [cursor=pointer]
    - button "修订审阅" [ref=e37] [cursor=pointer]
    - button "悬疑审计" [ref=e41] [cursor=pointer]
    - button "作者模型" [ref=e46] [cursor=pointer]
  - generic [ref=e50]:
    - main "项目概览" [ref=e52]:
      - complementary [ref=e53]:
        - generic [ref=e54]:
          - generic [ref=e55]:
            - heading "我的作品" [level=2] [ref=e56]
            - generic [ref=e57]: 1 个项目
          - button "雾港来信 本地小说项目" [ref=e59] [cursor=pointer]:
            - generic [ref=e63]:
              - generic [ref=e64]: 雾港来信
              - text: 本地小说项目
          - button "＋ 新建 / 找回作品" [ref=e65] [cursor=pointer]
        - generic [ref=e66]:
          - heading "快速入口" [level=2] [ref=e68]
          - generic [ref=e69]:
            - button [ref=e70] [cursor=pointer]
            - button [ref=e75] [cursor=pointer]
            - button [ref=e81] [cursor=pointer]
        - generic [ref=e86]:
          - strong [ref=e87]: 作者拥有最终决定权
          - text: Agent 提出候选；接受之后，内容才进入正式稿。
      - generic [ref=e88]:
        - generic [ref=e89]:
          - paragraph [ref=e90]: PROJECT / STORY WORKSPACE
          - heading "雾港来信" [level=1] [ref=e91]
          - paragraph [ref=e92]: 让每一个故事决定都有来处，让每一次修改都可以审阅。
          - button "继续这部作品 →" [ref=e93] [cursor=pointer]
        - generic [ref=e94]:
          - generic [ref=e95]:
            - text: "0"
            - paragraph [ref=e96]: 已保存章节
          - generic [ref=e97]:
            - text: "0"
            - paragraph [ref=e98]: 正文总字数
          - generic [ref=e99]:
            - text: "0"
            - paragraph [ref=e100]: 进行中的变更
          - generic [ref=e101]:
            - text: "0"
            - paragraph [ref=e102]: 故事事实与计划
        - generic [ref=e103]:
          - generic [ref=e104]:
            - heading "下一步" [level=2] [ref=e105]
            - generic [ref=e106]: 从当前项目出发
          - generic [ref=e107]:
            - button "先规划故事，再生成第一章 正文保存立即生效，后台分析不会覆盖你的编辑。 打开 →" [ref=e108] [cursor=pointer]:
              - generic [ref=e113]:
                - text: 先规划故事，再生成第一章
                - paragraph [ref=e114]: 正文保存立即生效，后台分析不会覆盖你的编辑。
              - generic [ref=e115]: 打开 →
            - button "审阅局部修改与候选 检查修改前后与依据，选择真正要进入作品的内容。 去审阅 →" [ref=e116] [cursor=pointer]:
              - generic [ref=e120]:
                - text: 审阅局部修改与候选
                - paragraph [ref=e121]: 检查修改前后与依据，选择真正要进入作品的内容。
              - generic [ref=e122]: 去审阅 →
      - complementary [ref=e123]:
        - generic [ref=e124]:
          - button "待处理工作 0 个变更" [expanded] [ref=e126] [cursor=pointer]:
            - generic [ref=e127]:
              - heading "待处理工作" [level=2] [ref=e128]
              - generic [ref=e129]: 0 个变更
            - generic [aria-hidden] [ref=e130]: ▴
          - generic [ref=e131]: 当前没有待处理变更。
        - generic [ref=e132]:
          - button [expanded] [ref=e134] [cursor=pointer]:
            - heading "最近任务" [level=2] [ref=e136]
            - generic [aria-hidden] [ref=e137]: ▴
          - generic [ref=e138]: 在创作台提交第一项规划任务。
        - link "⇩ 导出当前版本备份" [ref=e139] [cursor=pointer]:
          - /url: /api/v2/projects/prj_1d5d53d4377d4f9e/export
        - generic [ref=e140]: 作品事实、计划和正文统一保存在项目版本中。对话和任务结果是过程记录。
    - complementary "对话面板" [ref=e141]:
      - generic [ref=e142]:
        - generic [ref=e143]:
          - generic [ref=e144]:
            - button "新对话 ▾" [ref=e146] [cursor=pointer]:
              - generic [ref=e147]: 新对话
              - generic [ref=e148]: ▾
            - paragraph [ref=e149]: 项目内对话
          - button "新建会话" [ref=e150] [cursor=pointer]
          - button "关闭对话面板" [ref=e153] [cursor=pointer]
        - generic "对话消息" [ref=e157]:
          - paragraph [ref=e158]: 还没有消息。普通发送只进行对话；明确点击创作操作才会创建任务。
        - generic [ref=e159]:
          - generic [ref=e160]:
            - textbox "对话输入" [ref=e161]:
              - /placeholder: 讨论构想；Enter 发送，Shift+Enter 换行
            - button "发送" [disabled] [ref=e163]
          - generic [ref=e167]:
            - generic "发送前显示服务端当前选择；切换影响后续发送" [ref=e168]: 模型：qwen3:4b
            - button "＋ 引用" [ref=e170] [cursor=pointer]
            - button "✎ 创作操作" [ref=e171] [cursor=pointer]
  - contentinfo [ref=e172]:
    - generic [ref=e173]:
      - generic [ref=e174]: ● 雾港来信 · 主分支
      - generic [ref=e175]: 版本 —
    - generic [ref=e176]: ✓ 无未保存修改
```
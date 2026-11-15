# P1 演示 transcript · 案卷 Hastings

> 确定性回放：FakeProvider 预录事件流，无需 API key，任何人执行 `uv run python scripts/render_demo_transcript.py` 都能得到逐字相同的结果。

## 1. 用户输入

> 建立一个 1930 年代封闭列车上的谋杀案，先建两张角色卡，再写第一章。

## 2. Agent 决策链（终端渲染，CLI 真实渲染器输出）

```text
> 建立一个 1930 年代封闭列车上的谋杀案，先建两张角色卡，再写第一章。

先建第一张角色卡：波洛。
[tool] bible_upsert 写入角色 波洛
再建第二张：乘务长奎勒。
[tool] bible_upsert 写入角色 奎勒
动笔前核对一下账本。
[tool] bible_query 列出全部角色
账本没问题，开写第一章。
[tool] write_chapter 写入第1章（233 字）
案卷已开卷：波洛与奎勒已建档，第一章《雪封之夜》已落盘。下一章动笔前，我会先把凶手的不在场证明写进账本，避免第九章才补漏洞。
```

## 3. 落盘的工作区结构

```text
orient-express/
├── bible/characters/奎勒.yaml
├── bible/characters/波洛.yaml
└── chapters/ch01.md
```

## 4. 落盘文件真实内容

### `bible/characters/奎勒.yaml`

```yaml
name: 奎勒
identity: 东方快车乘务长
motivation: 掩盖十二年前的旧案，掌握全列乘务动线
secret: ''
relationships: []
notes: ''
```

### `bible/characters/波洛.yaml`

```yaml
name: 波洛
identity: 比利时侦探
motivation: ''
secret: 曾因一桩未破案离职，办案动机是好奇而非正义
relationships: []
notes: ''
```

### `chapters/ch01.md`

```markdown
# 第1章 雪封之夜

雪从午夜开始落。到三点十七分，列车已经完全被封在白色里，前后两头的瞭望窗只剩下模糊的灰。

波洛注意到车厢连接处的门是虚掩着的——列车在行驶中从不允许这件事。他把这个细节收进心里，像把一枚硬币放进背心口袋，不作声。

十二号包厢的门从里面锁着，但门缝下没有灯光。死者是位美国商人，而行李架上，还放着一只属于别人的手套。

“那位夫人说她在餐车喝过一杯酒，”乘务员奎勒的声音里有种不必要的确定，“我亲自送过去的。”波洛注意到他说的是“她”，而死者本人从未有过夫人同行。
```

## 5. 这份 transcript 证明了什么

| 主张 | 证据 |
|---|---|
| 模型不能绕过工具写文件 | 全部经 `bible_upsert` / `write_chapter` 落盘 |
| 动笔前先核对账本 | 剧本第三步是 `bible_query`，而非直接开写 |
| 格式一致性由工具保证 | 文件名为 `ch01.md`，标题由工具生成 |
| Agent 行为可确定性测试 | 同一份 FakeProvider 脚本可逐字复现 |

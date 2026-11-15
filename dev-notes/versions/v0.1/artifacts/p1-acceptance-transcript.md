# P1 真实端点验收 transcript

- 日期：2026-10-07
- 端点：DeepSeek `https://api.deepseek.com/v1`（OpenAI 兼容）
- 模型：`deepseek-v4-pro`
- 命令：`uv run python -m hastings demo-story`（stdin 管道喂入指令）
- 输入指令：为一桩密室谋杀案创建两位角色：一位侦探和一位嫌疑人，都写进 Story Bible，然后动笔写第一章。
- 结果：**通过**——验收标准 4 条全部满足（见下）

## Transcript（终端原文，见 [p1-raw-output.txt](p1-raw-output.txt)）

```text
案卷已打开：stories\demo-story
模型：deepseek-v4-pro｜工具：bible_upsert、bible_query、write_chapter
输入 /characters 查看账本，/quit 收工。

> [tool] bible_query 列出全部角色
Story Bible 为空，无既有设定冲突。现在将侦探与嫌疑人两张角色卡落盘。
[tool] bible_upsert 写入角色 顾沉舟
[tool error] bible_upsert 角色数据不合法：relationships: Input should be a valid list
[tool] bible_upsert 写入角色 沈澜
[tool error] bible_upsert 角色数据不合法：relationships: Input should be a valid list
工具拒绝了调用——`relationships` 需为列表。修正后重试。
[tool] bible_upsert 写入角色 顾沉舟
[tool] bible_upsert 写入角色 沈澜
两张角色卡已落盘。现在写第一章，出场角色（顾沉舟、沈澜）均已在 Bible 中建档。
[tool] write_chapter 写入第1章（1430 字）
第一章已完成。本次落盘与状态迁移如下：

**Story Bible（角色卡）**
- 新增 `顾沉舟.yaml`（侦探）：前刑警、独立顾问；动机是拆解一切密室并偿还旧案心债；秘密是十八年前搭档死于悬而未决的密室案，官方以自杀结案，他始终存疑。
- 新增 `沈澜.yaml`（嫌疑人）：被害人周鹤年的合伙人；动机表面是股权与分账纠纷，实则要守住不可告人的秘密；秘密是与周鹤年有未公开巨债，且对方握有一件能毁她名誉的旧物。备注已埋下关键能力线——她精通钟表、锁具与机关。

**正文**
- 写入 `chapters/ch01.md`（第1章《门从里面锁着》，1430 字）。

**本章为读者铺陈的线索（fair play，解谜所需已提前呈现）**
1. 密室构成：门内侧弹子锁钥匙插在锁孔、上方黄铜插销从内插死、窗户月牙锁从内扣死、无撬痕。
2. 门下沿与地板缝隙不足一指宽，塞不进钥匙、够不到插销。
3. 地板上有一道浅划痕，从门缝下方延伸到书桌脚边。
4. 周鹤年无外伤、脸色发青、颈侧有淡红淤痕、茶杯打翻半干，死因待尸检（死亡时间估算 6–8 点）。
5. 沈澜自述 7:30 到店发现、4 点最后见面、6:40 发消息未回；其怀表停在 10:17，而她称"坏了很多年"——时间点与其供述存在张力。
6. 桌上摊开的合同、掉落的钢笔、封死的壁炉与无天窗，收窄了密室出路。

角色设定与正文已通过工具完成核对与落盘，无矛盾。后续如需展开第二章（尸检结果、不在场证明核实等），我会先查询 Bible 再动笔。

> 故事已合上。
```

## 验收标准对照（DESIGN.md §8 P1 四条）

| # | 标准 | 证据 |
|---|---|---|
| 1 | REPL 启动、接真实 OpenAI 兼容端点（读 .env） | 输出第 1–2 行；provider = DeepSeek 兼容端点 |
| 2 | `bible_upsert` 落盘 2 张角色卡 | `stories/demo-story/bible/characters/{顾沉舟,沈澜}.yaml`，字段完整、中文未转义 |
| 3 | `write_chapter` 落盘 1 章正文 | `stories/demo-story/chapters/ch01.md`，`# 第1章 门从里面锁着` 头 + 1430 字正文 |
| 4 | 工具调用过程终端可见 | `[tool]`/`[tool error]` 摘要行 + 流式文本 |

## 观察记录

- **错误自我修正闭环有效**：模型首次把 `relationships` 写成非列表，`bible_upsert` 拒绝并回传字段级原因，模型下一轮修正成功——DESIGN.md §3.3「工具是账本的守门人」按预期工作。
- **动笔前先查询**：模型先调 `bible_query` 确认 Bible 为空再落盘，符合 system prompt 工作流程节。
- **fair-play 线索清单**：章节完成后模型主动列出本章铺陈的 6 条线索，与角色节守则一致。

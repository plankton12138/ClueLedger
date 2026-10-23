# 生成提示词

工具：内置 image_gen。以下为本次实际提交的三条完整提示词，均为新图生成，非修改现有产品截图。
写作台与时间线生成成功；章节概览两次返回 HTTP 403，未生成图片。

## 1. 写作台

```text
Use case: ui-mockup. Create ONE high-fidelity, production-believable Chinese desktop application screenshot, landscape 16:10, crisp readable Simplified Chinese typography, full interface edge to edge, no device frame, no perspective, no collage, no watermark, no promotional headline outside the interface.
Product: 线簿 / ClueLedger, a mystery-novel writing workbench. Novel name 雪山旅馆. This is a proposed UX direction for discussion.
Shared visual system: warm ivory paper content, pale cool blue-gray application chrome, dark charcoal text, quiet muted navy-blue accent, fine warm-gray divider lines. A little restrained classic desktop / early iOS craftsmanship in borders and serif editorial headings, but no glossy giant buttons, no heavy bevels, no strong gradients, no dark theme. Compact spaciously readable tool layout, sensible 16–18 px equivalent body labels, calm contrast, 8px spacing rhythm, modest corner radius. Content fills the available screen; avoid oversized empty margins, dashboard statistic cards and generic SaaS marketing aesthetics.
Persistent top navigation: product wordmark “线簿 ClueLedger”, book selector “雪山旅馆”, then “写作”, “故事资料”, “检查报告”, “活动记录”; settings and subtle “字号 100%” at the far end. Navigation looks like navigation with a clear selected underline, not a row of raised action buttons. Never show the old standalone buttons “刷新”, “索引”, “状态”. Only one visually dominant action per page. All long content is real-looking novel text and useful Chinese labels, not lorem ipsum or random characters.
Screen 1, active top navigation “写作”. Main interface is a full-height three-column writing desk. Left 18%: quiet chapter list heading “正文”, entries “第1章 来客”, “第2章 封山”, “第3章 走廊”, “第4章 钟声”, “第5章 门响”, “第6章 口供”, “第7章 怀表”, selected “第8章 雪夜”, followed by a separated “设定集” group with “人物小传”, “线索”, “真相”. Use simple rows rather than cards.
Center 55%: comfortable ivory read-only manuscript, title “第8章 雪夜”, subtle metadata “正文 · 3,246 字”, fine toolbar with “查看本章资料”. Large legible Chinese book serif prose, four short paragraphs:
“雪在午夜前停了。林远站在二楼走廊，听见楼下传来一声轻响。”
“他没有立刻下楼。那只停在十点十分的怀表，仍摆在窗边。”
“沈宁推开门，看了一眼墙上的钟。‘你也听见了？’”
“林远点头。走廊尽头的灯亮着，灯下却没有人。”
Keep the manuscript central, generous line spacing, subtle thin paper ruling optional, no text editing caret and no Save button.
Right 27%: conversation pane heading “写作助手”, conversation label “第8章修改”. User bubble “把第八章开头的雪景写得克制一点，保留门响和怀表。” Assistant response “已调整开头两段，门响与怀表的描写已保留。” Compact result line “已修改第8章 · 查看改动”. Bottom composer with optional reference chip “第8章 雪夜”, placeholder “告诉我你想怎样修改…”, one restrained blue “发送” button.
Strong separation between reading, file navigation and assistant. The image should demonstrate an understandable working writing page, not an analytics dashboard.
```

## 2. 章节概览

```text
Use case: ui-mockup. Create ONE high-fidelity, production-believable Chinese desktop application screenshot, landscape 16:10, crisp readable Simplified Chinese typography, full interface edge to edge, no device frame, no perspective, no collage, no watermark, no promotional headline outside the interface.
Product: 线簿 / ClueLedger, a mystery-novel writing workbench. Novel name 雪山旅馆. This is a proposed UX direction for discussion.
Shared visual system: warm ivory paper content, pale cool blue-gray application chrome, dark charcoal text, quiet muted navy-blue accent, fine warm-gray divider lines. A little restrained classic desktop / early iOS craftsmanship in borders and serif editorial headings, but no glossy giant buttons, no heavy bevels, no strong gradients, no dark theme. Compact spaciously readable tool layout, sensible 16–18 px equivalent body labels, calm contrast, 8px spacing rhythm, modest corner radius. Content fills the available screen; avoid oversized empty margins, dashboard statistic cards and generic SaaS marketing aesthetics.
Persistent top navigation: product wordmark “线簿 ClueLedger”, book selector “雪山旅馆”, then “写作”, “故事资料”, “检查报告”, “活动记录”; settings and subtle “字号 100%” at the far end. Navigation looks like navigation with a clear selected underline, not a row of raised action buttons. Never show the old standalone buttons “刷新”, “索引”, “状态”. Only one visually dominant action per page. All long content is real-looking novel text and useful Chinese labels, not lorem ipsum or random characters.
Screen 2, active top navigation “故事资料”. This is a dedicated full workspace page with no modal, no drawer and no dimmed background.
Left 17% subnavigation titled “故事资料”, selected “章节概览”, then “人物”, “时间线”, “线索与伏笔”, “名词表”.
Main page header “章节概览”, short helper “回顾各章内容，查看摘要与原文依据。” One blue primary button top right “整理章节”. Below: quiet filter line with search placeholder “搜索章节或内容”, filter “全部章节”, an understated actionable text “3章待整理”.
Below header, main content uses a master-detail split. Left of main content about 45%: readable table with columns “章节”, “内容概要”, “整理情况”; six useful rows, not decorative cards:
“第3章 走廊” | “林远发现走廊灯被人动过” | “已整理”
“第4章 钟声” | “旅馆内的钟声与证词出现分歧” | “已整理”
“第5章 门响” | “沈宁听到书房传来门响” | “已整理”
“第6章 口供” | “两份口供中的时间不一致” | “正文有改动”
“第7章 怀表” | “怀表停在十点十分” | “尚未整理”
selected “第8章 雪夜” | “雪停后，二楼再次传来门响” | “已整理”.
Right detail pane in the same page, about 55%: title “第8章 雪夜”, status “依据当前正文整理”. Section “本章概要”: two or three short readable lines about 林远 / 沈宁 / 走廊 / 怀表. Section “本章发生了什么” with three concise chronological bullet rows. Section “涉及人物” with 林远、沈宁 as understated text. Section “原文依据”, containing a bordered very pale ivory excerpt “雪在午夜前停了。林远站在二楼走廊，听见楼下传来一声轻响。” and a clear inline link “打开原文”. Avoid technical terms such as digest, cache, evidence ID, JSON or valid/stale. Strong table alignment, clear page hierarchy, restrained accent, no pop-up windows.
```

## 3. 时间线

```text
Use case: ui-mockup. Create ONE high-fidelity, production-believable Chinese desktop application screenshot, landscape 16:10, crisp readable Simplified Chinese typography, full interface edge to edge, no device frame, no perspective, no collage, no watermark, no promotional headline outside the interface.
Product: 线簿 / ClueLedger, a mystery-novel writing workbench. Novel name 雪山旅馆. This is a proposed UX direction for discussion.
Shared visual system: warm ivory paper content, pale cool blue-gray application chrome, dark charcoal text, quiet muted navy-blue accent, fine warm-gray divider lines. A little restrained classic desktop / early iOS craftsmanship in borders and serif editorial headings, but no glossy giant buttons, no heavy bevels, no strong gradients, no dark theme. Compact spaciously readable tool layout, sensible 16–18 px equivalent body labels, calm contrast, 8px spacing rhythm, modest corner radius. Content fills the available screen; avoid oversized empty margins, dashboard statistic cards and generic SaaS marketing aesthetics.
Persistent top navigation: product wordmark “线簿 ClueLedger”, book selector “雪山旅馆”, then “写作”, “故事资料”, “检查报告”, “活动记录”; settings and subtle “字号 100%” at the far end. Navigation looks like navigation with a clear selected underline, not a row of raised action buttons. Never show the old standalone buttons “刷新”, “索引”, “状态”. Only one visually dominant action per page. All long content is real-looking novel text and useful Chinese labels, not lorem ipsum or random characters.
Screen 3, active top navigation “故事资料”. A dedicated “时间线” workspace page, no modal or drawer.
Left 17% subnavigation titled “故事资料”: “章节概览”, “人物”, selected “时间线”, “线索与伏笔”, “名词表”.
Page header “时间线”, helper “按章节回看发生时间，对照原文核对顺序。” At the right a visible scope selector “查看截至第8章”, and ONE dominant blue button “从章节整理时间线”.
A quiet pale amber information strip below header: “第6—8章尚未整理，当前记录截至第5章。” Include simple text link “选择章节”, not another filled button.
Use a rich master-detail layout. Main middle area 52%: a readable vertical story timeline organized into “第一天 · 傍晚”, “第一天 · 夜里”, “第二天 · 清晨”. Each item shows chapter and event, with exact original time wording rather than invented machine timestamps:
“第1章 来客” / “傍晚六点” / “林远抵达旅馆”
“第2章 封山” / “入夜后” / “积雪封住山路”
“第4章 钟声” / “十点左右” / “走廊里响起钟声”
selected “第5章 门响” / “十点十分” / “沈宁听见书房门响”
“第5章 门响” / “第二天清晨” / “众人在大厅会合”.
Timeline is fine ruled lines and small blue dots, not huge colored cards or a complex graph. Show a subdued final dashed empty region “第6—8章 · 等待整理”.
Right 31% is a permanent in-page source inspector, not floating. Title “十点十分的门响”, subtitle “第5章 门响”. A structured row “原文时间：十点十分”, row “地点：二楼书房”. Heading “原文依据”, a generous readable quote: “墙上的钟刚过十点十分，沈宁听见书房方向传来一声门响。她停在楼梯口，没有再往前走。” A small adjacent inline action “打开第五章”. Beneath, a subtle separate section “相关记录” showing “第4章：钟声发生在十点左右” and neutral text “可对照两处原文核对先后”.
At bottom of main area show a slim finished task receipt, not a notification toast: “最近整理：第4—5章 · 已完成” and link “查看操作记录”. Emphasize that the task can be started and its results read within this page. No requirements to visit 人物状态 before organizing the timeline, no confidence percentages, no API or developer details.
```

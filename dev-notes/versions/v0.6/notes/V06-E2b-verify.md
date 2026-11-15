# V06-E2b 正文核对（派生记录）

## 目标

让「正文是否真的写到过」可以核对，而不是只靠作者声明。核对结果落在作品状态目录，
按源文件指纹判失效；失效的记录不再进入装配。

## 实际改动

| 位置 | 改动 |
|---|---|
| `workspace/disclosure.py` | 新增 `ObservedFact`、`ObservedEvidence`、`VisibleFact`、`DisclosureStore`（`disclosure.json`）、`publish_observed`；`build_disclosure_state` 接受 store 并合并核对结果 |
| `workspace/agent.py` | 工具模式增加 `state`：只读工具 + `save_disclosure`；`ToolMode` 扩展；装配入口透传 store |
| `workspace/service.py` | 用途 `update_disclosure` → `state` 模式；目标（小传）走原有 `index_paths` 槽位，1–10 份 |
| `workspace/api.py` | 受理用途增加 `update_disclosure`；`GET /state` 读作品状态目录里的记录 |
| `workspace/review.py` | 检查要求补一条：对照 `revealed` / `planned` 核对披露时点，按 disclosure 类问题报出 |
| `frontend` | 披露条目显示「·原文」来源标记，实体显示「核对已过期」 |

## 契约

`disclosure.json` 一条记录：

```json
{
  "entity_path": "设定集/角色小传/林静.md",
  "entity_digest": "…",
  "fact": "与死者有旧怨",
  "chapter": 3,
  "evidence": {"path": "正文/0003 旧怨.md", "digest": "…", "start": 12, "end": 21, "quote": "…"}
}
```

- **发布要过三关**：小传在当前指纹下可读、原文在当前指纹下可读、区间与引文完全一致。
  任一条不过，整批不写（`STATE_EVIDENCE_INVALID` / `STATE_SOURCE_STALE_CONFLICT` /
  `STATE_ENTITY_INVALID`）。
- **同一角色同一事实只留一条**：后来的核对结果覆盖旧的，不追加历史版本。
- **状态按当前磁盘算**：小传或原文任一被改过即 `stale`，文件没了即 `source_missing`；
  两种状态都不采用记录，退回「只按声明显示」，并给一条诊断。
- **装配优先级**：核对过的事实优先，声明只补没有证据的部分，同一事实不重复注入。

## 取舍

**整批发布，不做逐条写入。** 半点证据写进去，面板就会显示一条来源不明的「已核对」。
失败时作者重跑一次的成本，比留下半份记录小。

**记录不存正文，只存定位。** 路径、版本、区间与引文足够复核；存正文会让状态目录变成
第二份原稿，还会随每次改稿一起腐坏。

**失败不阻断写作。** 记录过期只是让这一条退回「声明」来源，写作继续；只有发布时才会因为
证据不合格报错。

**检查里加的是要求，不是确定性校验。** 「是否提前泄露」需要读语义，只能让模型按证据报出，
再由作者判断；报告因此仍是「证据存在」而不是「结论正确」。

## 验证

2026-10-23 执行：

| 检查 | 结果 |
|---|---|
| `uv run pytest -q --basetemp data/test-tmp/e2b-full -o tmp_path_retention_count=100` | 483 通过（新增 4 项记录用例、1 项工具用例） |
| `uv run ruff check src/hastings scripts/model_acceptance.py tests` | 通过 |
| `uv run mypy src/hastings` | 44 个源文件通过 |
| `npm test -- --no-file-parallelism`（frontend） | 11 个文件、105 项通过 |
| `npm run build`（frontend） | 通过 |

未验证：核对任务未在浏览器里走完整流程；在线模型未跑（同日已补，见下），抽取质量与检查误报率都没有数据。

## 追加：界面入口与读取工具（同日）

- 身份面板增加勾选与「核对选中」按钮，走同一套受理接口（`purpose=update_disclosure`，
  目标走 `index_paths` 槽位），发起后刷新面板。
- 新增 `read_state` 工具：不带参数给概览，带 `entity` 给该角色的三档条目、记录状态与原文
  依据。只在写作与核对模式可用；索引与检查模式的工具集保持原样（`test_chapter_index` /
  `test_review` 的契约不变）。
- 系统提示补一行：写到某个人物前先用它看披露进度，不凭印象推断读者知不知道。
- 补充验证：`pytest` 483 通过、`vitest` 106 项通过（新增 1 项发送用例）、`ruff` 与
  `mypy`（44 个文件）通过、`npm run build` 通过。

## 未完成

- **提前泄露的判定质量**：首个在线数据点（2026-10-23，`deepseek-flash`，`disclosure_extract`）零误报——第 3 章「没有说出旧怨」未被记为已披露；样本量 1，只说明可核对，不外推。
- **浏览器**：面板的勾选与发起仍没有端到端证据；在线模型已跑通「读原文—发布—下一章注入」闭环（报告：`../../artifacts/model-acceptance/20261023-112346-580100`）。

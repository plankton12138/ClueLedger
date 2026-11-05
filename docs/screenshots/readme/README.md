# README 截图

当前中英文 README 使用以下截图。更新日期：2026-11-05；版本 1.2.0；尺寸 1440 × 900。

| 文件 | 场景 | 原始截图 |
| --- | --- | --- |
| writing-v1.2.png | 章节只读稿纸与对话 | `02-readonly-chapter-and-chat.png` |
| revision-v1.2.png | 手动修订后，确认入稿前查看差异 | `12-revision-mode-diff.png` |
| style-v1.2.png | 编辑并保存作品级写作约定 | `09-style-document.png` |

截图来自当前代码的写作工作台浏览器用例。作品、章节和约定均为隔离测试数据，不使用作者真实作品，不调用在线或本地模型；直接截取真实页面，没有替换文字或合成界面。

在 `frontend/` 下运行以下命令，可生成本次对应的截图：

```powershell
$env:HASTINGS_E2E_PORT='8947'
$env:HASTINGS_E2E_DATA_DIR='C:/Projects/Agent/myagent/data/test-tmp/v13-reserved-20261105'
$env:HASTINGS_E2E_STATIC_DIR='C:/Projects/Agent/myagent/data/test-tmp/v13-reserved-20261105/static'
$env:HASTINGS_E2E_SCREENSHOT_DIR='C:/Projects/Agent/myagent/data/test-tmp/v13-reserved-20261105/screenshots'
npm run test:e2e:local -- writing-workbench.spec.ts
```

用例构建前端并启动独立服务；原始截图在指定的 screenshots 目录，按上表复制到本目录后逐张检查。

本目录未带版本后缀的旧图片为 2026-11-01 的历史材料，当前 README 不再引用；其中的构思时间轴与线索页面不属于当前版本。旧 `capture_readme_screenshots.cjs` 对应历史流程，不用于重拍当前版本。

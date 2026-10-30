# T066 实际执行记录

## 范围及来源

读取本 case `AGENTS.md`、`TASK.md`、`samples/T066/task.md`、`samples/shared/english.md`，以及显式引用的 `<local-RTK-instructions>`。未读取父级答案、其他 case、记忆、历史或其他产品回答。没有委派。

工具文档：Presentations SKILL.md、artifact-template-selection、style_guidelines、implementation、API_QUICK_START、finalization、native_evidence，以及 tables/charts API 和验证工具的必要接口。来源 [1] 为输入摘要所引的 Internal pilot log, 2026-10-20，仅引用其书目信息，没有访问底层日志。按 case 禁止访问真实客户资料的限制，没有搜索用户历史演示稿或连接私有资料。

使用 `mcp__codex_app__load_workspace_dependencies` 返回的 Node.js 与 `@oai/artifact-tool`，使用本地文件生成工作流。

## 命令及实际回执

所有 shell 调用均以 `rtk` 开头。主要实际命令如下（工作目录为本 case，工具标记命令从技能目录执行）：

1. `rtk read <local-RTK-instructions>` 及 `rtk read` 本 case 指令和样例，退出码 0。原文只有一段，记录 20、120、60、4/20、非因果限制及两个标识符。
2. `rtk proxy mkdir -p results/T066/build results/T066/previews`，退出码 0。
3. `rtk proxy ln -s <runtime-dependencies>/node/node_modules results/T066/build/node_modules`，退出码 0。
4. `rtk proxy fc-list : family`，退出码 0，列出可用字体。设计选用 Arial。
5. `rtk proxy node <local-path> --operation-kind create --expected-output-count 1 --output-format pptx`，退出码 0，仅调用一次。
6. 通过 `rtk proxy python3 - <<'PY'` 写入实际生成脚本 `build/create.mjs`。脚本使用原生文本框、表格、柱图和 speakerNotes，导出 10 张预览和候选 PPTX。
7. 初次执行 `rtk proxy .../node/bin/node results/T066/build/create.mjs` 返回原生表格校验声明缺失，已增加 `--require-native-table-slide 6`。后续校验发现默认字体 Aptos，已将文字样式属性修正为 `typeface: 'Arial'`。另补充运行时环境变量及单独的 deliverables 目录，分别解决 import 所需环境变量与私有校验报告位置要求。以上失败均发生在最终文件生成之前。
8. 最终执行 `rtk proxy env RUNTIME_NODE_MODULES=<runtime-dependencies>/node/node_modules <runtime-dependencies>/node/bin/node results/T066/build/create.mjs`，退出码 0。生成最终文件 52,086 字节，SHA-256 `afc298340ef823b23ef50a47e5b088bf25671617c53646526d6f9ab761bbc1b7`。
9. 最终工具回执保存于 `build/validation.json`：packageIntegrity finding_count=0，presentationLayout finding_count=0、warning_count=0、slide_count=10，字体为 Arial。第一方导入 slideCount=10。原生图表在第 4 页，原生表格在第 6 页，图表缓存及嵌入工作簿数值引用一致。工具未自动核对表格算术，故人工核对 4+16=20、4/20=20%、16/20=80%、比例合计100%。
10. `rtk proxy .../node/bin/node results/T066/build/render-final.mjs`，重新导入最终 PPTX 并渲染 10 张 final PNG。生成源见该脚本。使用 view_image 逐页查看幻灯片预览，检查可读性和布局。
11. 通过 `rtk proxy python3 - <<'PY'` 使用 zipfile/XML 解包最终文件：10 页、10 份非空 notes、1 个原生图表、1 个嵌入工作簿，第 6 页存在 a:tbl，两个标识符原样存在，ZIP CRC 无错误。回执 `verification.json`。首次图表计数规则只匹配 ppt/charts 路径，已按实际 ppt/slides/charts 路径修正并重新验证，得到 1。

## 内容核对和限制

计算口径：(120−60)=60 分钟，(120−60)/120=50%，4/20=20%，其余订单数20−4=16。没有把这些描述性计算当成因果估计，没有估计成本节约、推广规模或统计显著性。原始 Table 1 行未提供，汇总表清楚注明此限制。所有后续动作标记为建议。

所有文件写入本 case 的 results/T066。没有邮件、购买、公开发布或客户私有资料访问。没有原生 PowerPoint 应用测试，不据预览声称原生应用已验证。最终产物已实际生成，原始明细和因果验证不在现有材料中。

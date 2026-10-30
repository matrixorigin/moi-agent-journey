# T041 执行记录

## 读取来源

- 本 case 的 `AGENTS.md`、`TASK.md`、`samples/T041/task.md`。
- 唯一产品事实来源：`samples/shared/product.md`。该文件说明产品为虚构测试产品，支持人工上传 CSV/XLSX、含税运费对比、来源引用、经理审批及导出报告；列明 20 单试点耗时与未支持能力。
- 公共执行文档：`<local-RTK-instructions>`。
- 公共写作指导：`<local-path>` 及其 `references/ai-tells.md`。仅采用文案组织和表达规范，未引用其中市场统计。
- 未读取其他 case、其他答案、评分或历史记忆。

## 实际命令与回执

以下相对路径均在本 case 根目录下执行，所有 shell 调用均以 rtk 开头。

1. `rtk cat <local-RTK-instructions>`：退出码 0，读取命令前缀规则。
2. `rtk cat AGENTS.md TASK.md`：退出码 0，确认当前任务及写入目录。
3. `rtk cat samples/T041/task.md samples/shared/product.md`：退出码 0，获取写作要求与全部产品事实。
4. `rtk cat <local-path>`：退出码 0，读取写作指导。
5. `rtk cat <local-path>`：退出码 0，读取表达检查规范。
6. `rtk proxy python3 -c ...`：退出码 0，仅检查本 case 中 `.agents/product-marketing.md`、`.claude/product-marketing.md`、`product-marketing-context.md`。回执为三个文件均不存在，没有额外产品上下文。
7. `rtk proxy python3 - <<'PY' ... PY`：创建 `results/T041`，通过 `Path.write_text` 实际写入 `result.md` 与本执行记录。回执见下方交付核对。

## 事实与表达核对

- 三版分别突出采购判断与审批、财务成本复核、IT 文件流转及系统衔接。
- 试点原始口径为 20 单，平均耗时从 120 分钟降至 60 分钟。文案保留原始数字，不将其扩展为所有客户节约 50%，不推断金额节约，也不把它归因到某个角色的单独环节。
- CTA 为首页设计文案，未声称创建线上按钮、申请入口或发布网站。
- 已人工复读标题、正文和 CTA，核对不存在自动下单、ERP 自动写回、无限免费等正向承诺。未增加未经证实的财务自动处理、安全或部署能力。

## 限制与未执行项

任务为静态文案交付，未请求外部调研、产品操作或网站发布；未进行这些操作。产品输入未给出价格、套餐、部署、安全和权限信息，未补造。没有待补交的任务文件。

## 交付核对回执

写入命令退出码 0，实际生成 result.md（4802 字节、99 行）和 execution.md（初始 2567 字节、32 行）。随后执行 `rtk proxy python3 - <<'PY' ... PY` 回读两个 UTF-8 文件，均可读取；采购经理、财务、IT 三版标题各出现 1 次。本段为回读后追加的实际回执。

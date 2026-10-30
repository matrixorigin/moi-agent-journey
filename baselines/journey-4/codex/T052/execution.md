# 执行记录

## 读取来源

- 本 case `AGENTS.md`、`TASK.md`：任务范围、输入和交付要求。
- 公共工具规则 `<local-RTK-instructions>`：shell 命令以 rtk 开头。
- `samples/T052/task.md`：任务正文和指定来源。
- `samples/shared/tender.md`：完整招标材料，仅一段合成文本。

## 实际执行命令与回执

所有命令均以 `rtk` 开头，在本 case 根目录执行（公共规则读取使用绝对路径）。

1. `rtk read AGENTS.md`：退出码0，读取本 case 限制。
2. `rtk read TASK.md`：退出码0，确认任务T052及交付路径。
3. `rtk read <local-RTK-instructions>`：退出码0，读取RTK使用规则。
4. `rtk read samples/T052/task.md`：退出码0，确认提取五类内容。
5. `rtk read samples/shared/tender.md`：退出码0，实际读取资格、技术、交付和时间条款，以及并发数、数据量和保证金规则缺失信息。
6. `rtk proxy python3 - <<'PY' ... PY`：实际用Path.mkdir创建results/T052，并用write_text写入result.md、execution.md；命令成功回执见下方核对记录。
7. `rtk proxy python3 - <<'PY' ... PY`：读回两份交付文件并核对必需内容与文件大小，回执见下方。

## 提取和核对口径

不进行金额、日期或容量推算。投标截止保留为2026-11-12 17:00，不擅自补充时区；上线要求保留为签约30天，不推定签约日期或自然日/工作日口径。明确区分原文条款与推导的准备建议、待澄清问题。

## 限制及未完成部分

未提供招标完整文件、容量参数、保证金规则、验收细则和投标提交细节，因此只能列为待澄清，不能确认为已确定条款。未访问其他case、产品答案、历史或记忆，未执行外部提交操作。本任务指定的文本提取与文件交付已完成。

## 实际核对回执

写入命令退出码0，输出：Created results/T052/result.md and results/T052/execution.md。
读回核对命令检查15项必需字符串均存在，两份文件均非空且可读；命令退出码0。

# 执行记录

## 读取范围与来源

仅读取当前 case 的 `AGENTS.md`、`TASK.md`、`samples/T057/task.md`、`samples/shared/tickets.csv`，以及显式引用的公共命令规范 `<local-RTK-instructions>`。未读取其他 case、其他产品答案、评分、父目录答案、历史或记忆；未委派。

## 实际执行命令与回执

所有 shell 调用均以 `rtk` 开头，工作目录为当前 T057 case。

1. `rtk read AGENTS.md`：退出码 0，确认仅执行本任务、输出限定及读取边界。
2. `rtk read TASK.md`：退出码 0，确认知识库草稿交付要求。
3. `rtk read <local-RTK-instructions>`：退出码 0，确认 shell 使用 rtk 前缀。
4. `rtk read samples/T057/task.md`：退出码 0，输入为 tickets.csv，交付为 results/T057。
5. `rtk read samples/shared/tickets.csv`：退出码 0，读取全部 12 行工单，字段为 id、customer、date、topic、status。
6. `rtk proxy python3 - <<'PY' ... PY`：使用 csv.DictReader，以 utf-8-sig 读取 BOM；使用 Counter/defaultdict 汇总主题、客户、状态、日期和来源 ID，写入 `results/T057/analysis.json`。实际退出码 0；输出 12 条、12 个唯一 ID、日期 2026-10-11 至 2026-10-22，open 4 / closed 8；上传超时 6（50%）、运费缺失 3（25%）、如何导出 3（25%）。
7. `rtk proxy python3 - <<'PY' ... PY`：实际写入 `result.md` 与本执行记录；正文含三篇草稿及逐篇适用范围、来源工单和待审核项。

## 计算口径和核对

按唯一工单计数，输入无重复 ID；按 topic 原文精确分组。占比的分母是全部 12 张工单，不是客户数或独立事件数。两位客户分别为 C02、C01；所有上传超时来自 C02，其余主题来自 C01。没有对重复提单作事件级去重，也没有把 closed 当作已知解决方案。

## 限制与未完成项

源文件只有摘要字段，没有工单正文、处理步骤、产品版本、日志、权限说明或官方操作文档。已交付的是待审核文章草稿，未验证产品运行、根因、有效修复或具体导出路径；这些需补充材料后才能完成。未发送消息、公开发布或访问真实客户数据。

## 写入后核对

8. `rtk proxy python3 - <<'PY' ... PY`：重新读取 CSV、JSON 和正文，断言总数、来源 ID、3 篇文章及文件非空；退出码 0。

核对回执：3 个输出文件非空；12 个来源工单 ID 均出现在正文；主题计数之和为 12；3 篇 KB 草稿均存在。

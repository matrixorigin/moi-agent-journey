# T059 执行记录

## 读取来源

- 本case `AGENTS.md`、`TASK.md`：确认任务边界、交付目录及不得发信/承诺赔付要求。
- 公共指引 `<local-RTK-instructions>`：确认shell命令须以rtk开头。
- `samples/T059/task.md`：确认投诉处置任务及输入文件。
- `samples/shared/complaint.md`：唯一业务事实来源；原文为投诉、日志和合同资料的摘要。
- 未读取父目录内容、其他case、历史、记忆、其他产品答案或评分；未调用外部服务。

## 实际命令与回执

1. `rtk proxy cat AGENTS.md TASK.md`：退出码0，读取本case约束及任务说明。
2. `rtk proxy cat <local-RTK-instructions> samples/T059/task.md samples/shared/complaint.md`：退出码0，读取RTK规则、任务与业务摘要。
3. `rtk proxy python3 - <<'PY' ... PY`：以Python pathlib实际创建`results/T059`，写入`result.md`及本执行记录。正文包含事实边界、争议、缺失证据、责任分工、处理选项及未发送客户回复。
4. `rtk proxy python3 - <<'PY' ... PY`：实际读回两份产物，核对UTF-8可读、文件字节数与行数，并补入本条记录。读回时result.md为6298字节、52行；execution.md为1727字节、22行（本条补入后记录大小随之变化）。

## 计算口径与执行边界

无金额或故障时长计算。保留原始数字：40次失败、涉及12客户、该客户失败3次、09:15恢复；两小时延迟与5万元均标记为客户主张。没有原始日志、合同全文、客户损失凭据和实际沟通记录，故未确认根因、业务恢复、责任或应赔金额。

已完成文本方案与回复草稿；未发送任何消息，未联系客户或人员，未指派真实负责人、查询真实客户系统、批准或承诺赔付。负责人、后续更新时间和审批均需实际负责团队确认。草稿有明确占位符与发送前核对要求。

# 执行记录

- 任务：T060 续费跟进排序；分析日期固定为 2026-10-29。
- 已读取：本 case `AGENTS.md`、`TASK.md`、`samples/T060/task.md`、`samples/shared/renewals.csv`；必要公共执行规范 `<local-RTK-instructions>`。
- 未读取其他 case、历史、记忆或其他产品答案；未委派。

## 实际命令与回执

所有 shell 命令均以 `rtk` 开头，在本 case 根目录执行（公共规范使用绝对路径）：

1. `rtk read AGENTS.md`、`rtk read TASK.md`：成功读取任务范围和交付要求。
2. `rtk read <local-RTK-instructions>`、`rtk read samples/T060/task.md`、`rtk read samples/shared/renewals.csv`：成功读取规范及原始 3 行客户数据。
3. `rtk proxy python3 - <<'PY' ... PY`：使用 Python 标准库 csv，以 utf-8-sig 处理 CSV BOM；用 datetime.date 计算自然日差，实际写入 result.md 与本记录。

计算输出：
```
C01 days_to_renewal= 33 value= 100000 active_trend= up open_tickets= 0
C02 days_to_renewal= 7 value= 150000 active_trend= down open_tickets= 4
C03 days_to_renewal= 17 value= 20000 active_trend= zero open_tickets= 1
customers= 3 total_value= 270000
```

排序采用明确的业务判断：临近续费和使用风险优先，结合工单数与金额，不使用未经验证的流失概率或任意量化评分。

## 限制

原始数据未提供币种、统计周期、工单严重程度或客户意向，结果已明确区分事实、推断与建议。未访问真实客户私有资料，未发送邮件、创建日程或执行对外沟通；沟通提纲与日期均为建议。任务所需文本交付已生成，无需外部工具或外部事实检索。

## 产物验证回执

实际执行 `rtk proxy python3` 重新读取两份 UTF-8 文件，均非空；确认正文包含全部 3 个客户、续费天数及金额合计。验证命令退出码为 0。

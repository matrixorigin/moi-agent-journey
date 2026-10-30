# 执行记录

## 读取来源

只读取本case的`AGENTS.md`、`TASK.md`、`samples/T058/task.md`及指定的` samples/shared/tickets.csv`、`samples/shared/usage.csv`；另按指令读取公共shell规范`<local-RTK-instructions>`。未读取其他case、历史、记忆、其他产品答案或评分。

## 实际执行及回执

所有shell工作目录为本case（读取RTK规范的首次命令使用会话初始目录，目标为绝对路径），命令均以rtk开头。

1. `rtk read <local-RTK-instructions>`，exit 0：确认shell必须以rtk开头，可用`rtk proxy`运行原始命令。
2. `rtk read AGENTS.md TASK.md`，exit 0：确认任务范围和交付路径。
3. `rtk read samples/T058/task.md samples/shared/tickets.csv samples/shared/usage.csv`，exit 0：取得指定输入。tickets共12条，其中C02为6条；usage共6条，其中C02为2条。
4. 实际执行`rtk proxy python3 -c '…'`：使用Python标准库csv以utf-8-sig读取两CSV，筛选customer=C02，按status计数，以10月/9月−1计算变化，以errors/runs计算比例，以2026-10-29减去open工单date计算开立天数；创建`results/T058`并写入`metrics.json`，exit 0。
   - 返回：工单6、open 4、closed 2，主题仅“上传超时”。
   - 9月：active_days=18、runs=80、errors=3；10月：2、5、4。
   - 变化：−88.89%、−93.75%、+33.33%；错误数/运行次数：3.75%、80%。
   - K01—K04距报告日分别18、17、16、15天。
5. 使用apply_patch实际写入`result.md`与本执行记录。未调用外部服务或发送客户消息。
6. 实际执行`rtk proxy python3 -c '…'`读取三份产物、检查非空并核对JSON中open+closed=ticket_count=6，exit 0。返回：result.md 4892 bytes、execution.md 2176 bytes（补录本条前）、metrics.json 533 bytes；所有文件可读取且计数4+2=6一致。文件大小仅作写入回执，以最终文件为准。

## 口径与限制

报告是基于提供快照的运营判断。没有日志、日粒度使用、客户反馈、合同、SLA或续约信息；不能证实上传超时导致使用下降、错误比例等同失败率，或客户已流失。10月统计截止时间未给出，不能视为完整月份。工单开立天数以报告日期计算，当前状态是否最新待核查。

后续行动均明确标为建议，没有声称已经排障、修复、联系客户或完成恢复。任务要求的本地报告和计算明细已生成；上述外部跟进不是本次实际执行内容。

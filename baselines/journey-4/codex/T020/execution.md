# 执行记录

## 读取来源与边界

- 本case的 `AGENTS.md`、`TASK.md`、`samples/T020/task.md`。
- 用户指定公共指令 `<local-RTK-instructions>`。
- 唯一业务来源：`samples/shared/decision.md`，全文为：
  > 决策：是否上线自动下单。支持：每单节约20分钟，经理愿意试点。反对：报价缺运费占18%，ERP接口未打通，撤单规则未知。上月100单中12单被人工改选。试点预算3万元，团队2人可投入两周。
- 未读取父目录文件、其他case、历史、记忆或其他产品答案；未委派；未联网，未访问业务系统、下单或发送消息。

## 实际命令与回执

所有shell命令均以 `rtk` 开头，工作目录为本case根目录。

1. `rtk read AGENTS.md`：退出码0；读取任务隔离及RTK约束。
2. `rtk read TASK.md`：退出码0；确认任务与指定输入/输出。
3. `rtk read <local-RTK-instructions>`：退出码0；确认命令前缀及 `rtk proxy` 用法。
4. `rtk read samples/T020/task.md`：退出码0；确认正反论证及执行记录要求。
5. `rtk read samples/shared/decision.md`：退出码0；获得上述唯一业务材料。
6. `rtk proxy python3 - <<'PY' ... PY`：使用Python pathlib创建 `results/T020` 并写入 `result.md` 与本记录。具体脚本内容为当前执行工具调用中的两个Markdown字符串，不运行任何业务操作。
7. 最终核对命令及回执见下文“产物核对”。

## 数据核对与计算口径

- 12÷100＝12%，仅称历史人工改选率。
- 100×20÷60＝33.333…小时，正文保留1位小数；仅为假定所有100单均适用且20分钟为净节约的潜在值。
- 2人×2周＝4人周，未擅自转换为工作日或小时。
- 18%的分母未知，不与12%合并，不据此计算自动化覆盖率。
- 所有阶段门槛及试点安排均明确标为建议，未作为输入中的事实。

## 限制与未完成项

输入没有订单明细、报价、接口文档、撤单规则、成本明细或试点实测，无法证实净收益、改选原因及系统控制有效性。任务要求的论证与文件交付已完成；生产上线、实际试点、ERP联调及补充事实调查未执行，亦未被本任务要求执行。结论是条件性决策建议，不是上线批准。

## 产物核对

执行 `rtk proxy python3 - <<'PY' ... PY`，读取两份产物并检查非空、UTF-8可读；用assert核对12%、33.3小时和4人周。退出码0。回执：result.md exists, UTF-8 readable；execution.md exists, UTF-8 readable；Arithmetic verified: 12%; 33.3 hours; 4 person-weeks。写入命令退出码0，回执为 Written results/T020/result.md and results/T020/execution.md。

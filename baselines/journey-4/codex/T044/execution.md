# 执行记录

## 来源

仅读取本 case 的 AGENTS.md、TASK.md、samples/T044/task.md、samples/shared/content.csv，以及明确引用的公共命令规则 <local-RTK-instructions>。没有读取其他 case、父目录答案、历史或记忆，没有外部发布或消息发送。

## 实际执行

1. `rtk read AGENTS.md`、`rtk read TASK.md`：读取任务范围、输出要求。
2. `rtk read <local-RTK-instructions>`、`rtk read samples/T044/task.md`、`rtk read samples/shared/content.csv`：读取命令规则和原始 CSV。输入表字段为 id/topic/views/leads/cost，共 4 条内容。
3. 实际通过 `rtk proxy python3 - <<'PY'` 执行 Python 标准库脚本：使用 utf-8-sig 读取 CSV，将 views/leads/cost 转为整数；逐行校验正浏览量、正线索数和非负成本；计算 leads/views、cost/leads 和每百成本线索数，按 CPL 升序；实际写入 result.md 与 metrics.csv。

执行回执：

```text
INPUT_ROWS 4
TOTALS {'views': 16800, 'leads': 105, 'cost': 3800}
P3 运费案例 rate=5.00% CPL=7.50
P1 报价实操 rate=5.00% CPL=10.00
P2 品牌故事 rate=0.20% CPL=100.00
P4 抽奖 rate=0.05% CPL=400.00
CORE_COST_SHARE 21.0526% CORE_LEAD_SHARE 85.7143%
```

4. 再次实际使用 `rtk proxy python3 - <<'PY'` 回读 metrics.csv 与 result.md：断言顺序 P3/P1/P2/P4、总浏览 16800、总线索 105、总成本 3800、CPL 7.50/10.00/100.00/400.00，并核对正文核心线索占比 85.71%。校验完成后写入本 execution.md。

## 计算口径

整体转化率采用总线索/总浏览＝105/16800＝0.625%，显示为 0.62%（Python 默认舍入）；整体 CPL＝3800/105＝36.19。不是各内容比率的简单平均。核心两类成本占比＝800/3800，线索占比＝90/105；组合 CPL＝800/90＝8.89。成本未标明单位。

## 交付与限制

交付 result.md（效果判断、下一轮四个选题和验证建议）、metrics.csv（可复核计算表）、execution.md（本记录）。所有输出位于 results/T044。

原始表只有四条内容，无日期、渠道、有效线索或成交数据；未进行真实投放、销售核验或成交归因。预算分配、14 天观察窗口与选题为后续建议，未声称已执行。文件分析与产物生成已完成，无工具缺失导致的未完成项。

# 执行记录

## 来源
- 本 case AGENTS.md、TASK.md：任务范围与交付要求。
- <local-RTK-instructions>：shell 命令以 rtk 开头。
- samples/T080/task.md：核对成本、计算已知毛利、漏项待确认。
- samples/shared/cost.md：报价 200000；硬件 80000、实施 40000、培训 10000、云资源 2000/月 × 12；缺运费、差旅、税费、后续维护人力及报价有效期。

## 实际执行
1. `rtk proxy cat AGENTS.md TASK.md`：退出码 0，读取本 case 指令及任务。
2. `rtk proxy cat <local-RTK-instructions> samples/T080/task.md samples/shared/cost.md`：退出码 0，取得输入原文与 RTK 规则。
3. `rtk proxy python3 -c ...`：使用 Decimal 实际计算并写入 result.md 与本记录。标准输出：云资源=24000；已知成本=154000；已知毛利=46000；已知毛利率=23.00%。

## 计算与限制
- 2000 × 12 = 24000；80000 + 40000 + 10000 + 24000 = 154000。
- 200000 − 154000 = 46000；46000 / 200000 × 100 = 23%。
- 未列费用金额、税务口径及报价有效期无法从输入确定，全部保留待确认；没有推定为 0，没有给出完整净利润。
- 未访问其他 case、历史/记忆或其他产品答案；未发送、下单或发布。

4. `rtk proxy python3 -c ...`：回读 result.md 并检查两份文件存在且非空，退出码 0；正文数值与计算回执一致。

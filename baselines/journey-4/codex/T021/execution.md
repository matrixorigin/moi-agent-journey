# T021 实际执行记录

## 来源与范围

工作目录：`<case-root>/T021`。

业务数据仅使用 `samples/shared/sales.csv`（UTF-8 BOM，16条记录），任务要求读取自本目录 `AGENTS.md`、`TASK.md` 与 `samples/T021/task.md`。没有查询网络，没有读取其他case、历史、记忆、其他产品回答或评分资料，没有委派。

公共工具说明读取：`<local-RTK-instructions>`；CSV分析参考 Spreadsheets 技能 `.../spreadsheets/26.1017.22136/skills/spreadsheets/SKILL.md` 的只读分析规则。该技能读取输出因长度截断；本任务没有创建或编辑工作簿，仅以Node标准库分析CSV并交付Markdown。

## 实际命令和回执

所有shell命令均以 `rtk` 开头，且以本case为工作目录：

| 命令 | 实际回执 |
|---|---|
| `rtk read AGENTS.md` | exit 0；获得本case限制 |
| `rtk read TASK.md` | exit 0；确认T021任务及交付位置 |
| `rtk read <local-RTK-instructions>` | exit 0；确认shell须以rtk开头 |
| `rtk read samples/T021/task.md` | exit 0；确认输入为sales.csv |
| `rtk proxy head -n 8 samples/shared/sales.csv` | exit 0；核对表头及订单行结构 |
| `rtk read <local-path>` | exit 0；长输出被截断，未以此声称完整阅读 |
| `rtk read samples/shared/sales.csv` | exit 0；完整读取16条订单 |
| `rtk proxy node -e '…'` | exit 0；一次内联探索计算，读取CSV、筛选paid、聚合月份/产品、核对ID与金额、输出排除订单与客户集合；同一逻辑随后保存为可复跑的analyze.js并重新执行，非仅提供代码示例 |
| `rtk proxy node results/T021/analyze.js` | exit 0；实际生成calculation.json，完整执行代码保存在analyze.js |

文件通过 `apply_patch` 实际写入，只写本case的 `results/T021`。最终交付正文为 `result.md`，计算证据为 `calculation.json`，复跑脚本为 `analyze.js`，本文件为执行说明。

## 计算与核对结果

- 记录状态：paid 15条，cancelled 1条；没有重复order_id，金额与qty×unit_price不一致记录为0。
- 9月：6笔、6客户、销量6、销售额900、频次1、客单价150。
- 10月：9笔、6客户、销量18、销售额2400、频次1.5、客单价266.6666666666667。
- 两月客户集合均为C01—C06。排除订单OX，日期2026-10-10，客户C07，产品A，金额100，status=cancelled。
- 产品A：300→1200，增量900；产品B：600→1200，增量600；各月产品总和与总体销售额一致，`productReconciles=true`。
- 顺序替代法（客户数→频次→客单价）：0、450、1050；Node浮点计算末项为1050.0000000000002，差额远小于1e-9，`bridgeReconciles=true`。正文按合理精度展示。
- 原始行逐条可见：A单价始终100，B始终200；9月每单1件、10月每单2件；C01—C03十月均下2笔订单，C04—C06均为1笔。
- 最终实际执行 `rtk proxy node -e '…'` 读取四个保存文件并检查非空、解析calculation.json、核对两项reconciliation及10月收入2400；exit 0。输出：result.md 3867 bytes、execution.md 3414 bytes（追加本条前）、analyze.js 2591 bytes、calculation.json 2053 bytes；`Saved artifacts verified; revenue delta=1500`。

## 限制与未完成部分

要求的分析和文件均已完成。没有外部业务系统可核对自然月完整性，未补造支付时间、退款、利润或原因数据；金额单位原文件未注明。按订单日期和paid状态分析，不声称现金流、完整月结业绩或因果关系。没有要求生成Excel或图表，因此未额外生成工作簿。

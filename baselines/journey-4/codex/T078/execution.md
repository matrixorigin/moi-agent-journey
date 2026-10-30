# T078 执行记录

## 来源与范围

读取本 case 的 `AGENTS.md`、`TASK.md`、`samples/T078/task.md`，以及唯一业务输入 `samples/shared/supplier_orders.csv`。另外读取所引用的公共 `<local-RTK-instructions>` 和 Spreadsheets `SKILL.md` 工具说明。未读取其他 case、其他产品答案、历史或记忆。所有新增文件均在 `results/T078`。

源文件 SHA-256：`2569918a0702aa3a5702ad7d046d9da944d4c305674373a1271fdeb7f8875e43`。

## 实际命令与回执

工作目录为当前 T078 case 根目录。以下 shell 命令均已执行并退出 0：

1. `rtk read AGENTS.md`、`rtk read TASK.md`：读出任务及范围约束。
2. `rtk read <local-RTK-instructions>`：读出 shell 使用 rtk 的约束。
3. `rtk read samples/T078/task.md`：确认按供应商计算三项指标，交付 Markdown。
4. `rtk read samples/shared/supplier_orders.csv`：读取表头及 S01–S04 四行数据，识别 UTF-8 BOM 和 CRLF。
5. `rtk read <local-path>`：读取 CSV 分析相关工具要求；输出发生截断。本任务没有创建或编辑工作簿。
6. `rtk proxy mkdir -p results/T078`：创建交付目录。
7. 使用文件补丁工具写入 `results/T078/analyze.mjs`，随后实际运行 `rtk proxy node results/T078/analyze.mjs`。脚本用 Node.js 读取 CSV、去除 BOM、解析数字、按供应商聚合，检查订单号唯一、数量与缺陷范围、日期格式，计算指标并写出 `result.md` 和 `calculations.json`。
8. `rtk read results/T078/result.md`：回读生成的报告，确认两家供应商公式、结果、四笔订单明细和数据限制已写入。

脚本真实输出摘录：

```text
totals: orders=4, qty=500, defects=6, onTime=3
V1: n=2, onTime=1, qty=200, defects=4,
    onTimeRate=0.5, weightedDefectRate=0.02,
    firstPrice=10, lastPrice=11, priceDelta=1, priceChange=0.1
V2: n=2, onTime=2, qty=300, defects=2,
    onTimeRate=1, weightedDefectRate=0.006666666666666667,
    firstPrice=12, lastPrice=12, priceDelta=0, priceChange=0
Written: result.md, calculations.json
```

## 计算与数据核对

- S01 同日交付、S03 提前一天、S04 同日交付，均准时；S02 晚两天，共 3 笔准时。
- V1 数量 100+100=200、缺陷 1+3=4；V2 数量 200+100=300、缺陷 2+0=2。供应商合计与源文件总计 500 件、6 件缺陷一致。
- V2 数量加权缺陷率采用 2/300=0.666666…%，报告为 0.67%；未使用逐单缺陷率简单平均得到的 0.50%。
- 价格按应交日期首末比较，V1 (11−10)/10=10%，V2 (12−12)/12=0%。公式及订单对应关系均在正文明确。

## 产物与限制

已生成 `result.md`（业务结果）、`calculations.json`（未四舍五入的计算值）、`analyze.mjs`（实际运行的计算脚本），并记录本执行说明。

无所需交付未完成。输入没有下单时间、SKU、币种、单位、合同价，报告明确采用应交日期排序并注明价格可比性假设，未扩展为对真实供应商长期表现的判断。

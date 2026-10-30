# 人工流程自动化交付

已将 `samples/repo/manual.md` 转为 Python 3 标准库脚本 `automate_sales.py`。通过参数接收输入路径，支持 UTF-8 BOM、dry-run、客户金额汇总、异常清单和 JSON 报告。无第三方依赖。

## 使用

在本 case 根目录执行：

```bash
rtk proxy python3 results/T088/automate_sales.py --input samples/shared/sales.csv --output-dir results/T088/data --dry-run
rtk proxy python3 results/T088/automate_sales.py --input samples/shared/sales.csv --output-dir results/T088/data
```

dry-run 只向标准输出打印报告，不创建输出目录、不写报告文件。正式执行生成 `data/summary.csv`、`data/exceptions.csv`、`data/report.json`；再次执行覆盖同名结果，不追加重复行。目录中其他文件不清理。

## 计算口径及结果

将手工说明中的 `input.csv` 映射为任务给定的 `samples/shared/sales.csv`，`customer` 映射为实际列 `customer_id`。按输入顺序对 `order_id` 保留首条，然后仅纳入 `status` 精确等于 `paid` 的记录；使用 Decimal 汇总 `amount`，不根据数量和单价重新计算，不限定月份。客户按 ID 排序。

输入 16 条，重复 0 条；排除 cancelled 1 条，纳入 paid 15 条；6 个客户合计 3300。

| 客户 | 汇总金额 |
|---|---:|
| C01 | 500 |
| C02 | 500 |
| C03 | 500 |
| C04 | 600 |
| C05 | 600 |
| C06 | 600 |

异常清单记录所有被排除行、原始字段、输入行号及原因。此输入只有第 17 行订单 OX，原因 non_paid_status。缺失订单 ID、缺失 paid 客户 ID、非法或非有限金额也会记录为异常；非 paid 行不做金额验证。重复行始终记重复原因；即使首条 cancelled，后续同单号 paid 也不纳入。

## 实际验证

`verify.py` 已执行，详细回执保存在 `verification.json` 和 `execution.md`：新输出路径 dry-run 前后文件树完全一致且目录未创建；已有产物上的 dry-run 同样无变化；两次正式执行产物逐文件 SHA-256 相同；输入 SHA-256 未变。另用合成夹具验证去重先于 paid 筛选、重复订单、非法字段，以及 0.1 + 0.2 = 0.3。

可重跑验证命令：

```bash
rtk proxy python3 results/T088/verify.py
```

未完成项：无。适用边界：脚本将数据加载到内存，适合可装入内存的 CSV；每个产物独立原子替换，三个文件不构成整体事务，不应并发写同一个输出目录。金额不附币种，沿用输入口径。

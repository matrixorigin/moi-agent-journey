# T083 数据处理结果

已根据 manual.md 编写并实际执行 `process_sales.py`，先 dry-run，再连续正式运行两次。输入未修改，第二次运行没有追加数据，三项数据输出的 SHA-256 与第一次完全一致。

| 客户 customer_id | 汇总 amount |
|---|---:|
| C01 | 500 |
| C02 | 500 |
| C03 | 500 |
| C04 | 600 |
| C05 | 600 |
| C06 | 600 |
| 合计 | 3300 |

原始记录 16 条，唯一 order_id 16 个，纳入汇总 15 条，客户 6 个；重复订单 0 条。异常列表共 1 条：CSV 第 17 行 OX，客户 C07，金额 100，状态 cancelled，原因为 non_paid，不计入汇总。

处理口径：按输入顺序对 order_id 保留首条，再筛选 status 精确等于 paid 的记录；以实际字段 customer_id 对 amount 求和。manual.md 中 input.csv 对应本任务指定的 sales.csv，customer 对应 customer_id。金额用 Decimal 处理，不使用浮点数；未擅自按月份、渠道筛选或用 qty × unit_price 替换 amount。异常保留源行号与关键字段；重复项会记录首次出现行号。CSV 输入按 UTF-8 BOM 兼容读取。输出每次重新计算、覆盖写入，客户按编号排序。

交付文件：

- process_sales.py：可重复运行的处理脚本，仅依赖 Python 3 标准库。
- summary.csv：客户金额汇总。
- exceptions.csv：异常和排除记录。
- statistics.json：数量、总金额、输入哈希。
- verify_run.py：实际执行 dry-run 和两次写入、核对结果的验证脚本。
- verification.json：真实子进程命令、退出码、标准输出、前后哈希和核对结果。
- execution.md：来源、执行过程及限制。

在本 case 根目录运行：

```sh
rtk proxy python3 results/T083/process_sales.py samples/shared/sales.csv --output-dir results/T083 --dry-run
rtk proxy python3 results/T083/process_sales.py samples/shared/sales.csv --output-dir results/T083
```

重现全部核验：`rtk proxy python3 results/T083/verify_run.py`。

未完成项：无。当前样本没有重复订单，因此真实样本执行未触发重复订单分支；第二次执行不重复累计已通过输出字节哈希和记录数核对。

# 执行记录

来源：本 case 的 AGENTS.md、TASK.md、samples/T071/task.md，以及 samples/shared/invoices.csv、samples/shared/payments.csv；另读取指令引用的 <local-RTK-instructions>。未读取其他 case、历史或评分。

实际命令：
1. `rtk proxy cat AGENTS.md TASK.md`，退出码 0，取得任务与目录约束。
2. `rtk proxy cat <local-RTK-instructions> samples/T071/task.md samples/shared/invoices.csv samples/shared/payments.csv`，退出码 0，读取两份完整输入。
3. `rtk proxy python3 - <<'PY' ... PY`，实际运行 Python 标准库 csv、Decimal 和 pathlib，生成 result.md、exceptions.csv、reconciliation.csv、verification.json。退出码 0。
4. 本次 `rtk proxy python3` 写入 execution.md 并回读交付目录与 CSV，输出文件清单和数据。

计算过程：用 utf-8-sig 去除 BOM，将源文件中的字面量反斜杠 r、反斜杠 n 行分隔符转换为实际换行，再用 CSV 解析。按 invoice 精确匹配，Decimal 累计金额；差额为付款总额减发票金额。同一发票两笔同额付款标记疑似重复；无付款列为未付，已有付款但汇总不符列为金额不一致。

真实运行回执：invoice_count=4，payment_count=4，invoice_total=7500，paid_total=6200，net_difference=-1300，gross_outstanding=3300，overpayment=2000，exception_rows=4，csv_readback=ok。

核对：全部付款均匹配已有发票；生成的两个 CSV 均重新读取，完整对账 4 行、异常 4 行。I02 两个类别为同一异常发票，汇总金额只计算一次。I03 未付款 3000、I04 少付款 300，减去 I02 超付款 2000，得到总净欠付 1300，与 7500-6200 一致。

限制：仅核对样例，未访问真实客户数据。缺少币种、银行交易流水、付款状态及冲正记录，不能确认重复付款的实际结算状态，也不能判定 P02 或 P03 哪笔应撤回。未付不等于逾期，已在正文区分到期日。文件生成与核对已完成，无其他未完成交付。

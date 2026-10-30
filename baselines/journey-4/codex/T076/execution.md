# 执行记录

工作目录：`<case-root>/T076`。

## 实际读取来源

通过exec_command实际执行以下命令，均退出码0：

```text
rtk read AGENTS.md
rtk read TASK.md
rtk read <local-RTK-instructions>
rtk read samples/T076/task.md
rtk read samples/shared/invoices.csv
rtk read samples/shared/payments.csv
```

发票表读取4条数据：I01/S1/1000/2026-10-01，I02/S2/2000/2026-10-15，I03/S3/3000/2026-11-15，I04/S4/1500/2026-10-28。
付款表读取4条数据：P01/I01/1000，P02/I02/2000，P03/I02/2000，P04/I04/1200。CSV含UTF-8 BOM，脚本使用utf-8-sig读取。

## 实际计算及回执

通过apply_patch在本交付目录写入`analyze.py`，随后实际运行：

```text
rtk proxy python3 results/T076/analyze.py
```

退出码0。脚本生成`receivables.csv`、`duplicate_payments.csv`与`reconciliation.json`，真实标准输出如下：

```json
{
  "as_of": "2026-10-29",
  "invoice_count": 4,
  "payment_count": 4,
  "invoice_total": "7500",
  "raw_payment_total": "6200",
  "applied_payment_total": "4200",
  "suspected_duplicate_total": "2000",
  "remaining_total": "3300",
  "overdue_total": "300",
  "not_due_total": "3000",
  "reconciliation": "7500 = 4200 + 3300; 6200 = 4200 + 2000"
}
```

实际执行的断言：付款发票编号均匹配；逐发票剩余应收不为负；7,500＝4,200＋3,300；6,200＝4,200＋2,000；六项汇总与期望算术值一致。结果正文及邮件草稿通过apply_patch实际写入`result.md`。

## 限制及未执行项

生成后实际执行`rtk read results/T076/receivables.csv`与`rtk read results/T076/duplicate_payments.csv`，均退出码0，确认4条发票记录及P03单独列示。另以`rtk proxy python3 -c`实际读取6个交付文件、CSV与JSON并执行断言，退出码0，输出：`Verified 6 non-empty deliverables; 4 invoice rows; P03 separately listed; I04 remaining=300, overdue_days=1; remaining_total=3300`。

未提供币种、付款日期、银行交易流水号、对方名称或邮箱。疑似重复仅依据发票编号及金额一致识别，不能证实银行实际重复支付。暂按所有付款均在分析日前发生计算，并将P03暂挂，不跨对方抵扣。无法完成真实到账日期校验、重复款项性质确认或对方身份确认；这些限制均在结果中说明。

没有发信、访问真实客户资料、退款、改动输入文件、公开发布或委派。没有读取其他case、父目录答案、历史或记忆。

# 实际执行记录

## 来源与边界

仅读取本 case 的 `AGENTS.md`、`TASK.md`、`samples/T074/task.md`、`samples/shared/quotes.csv`，以及指令要求的公共规则 `<local-RTK-instructions>`。未读取其他 case、历史、记忆、评分或其他产品答案。未联网、发信或下单。

## 实际命令与回执

在本 case 根目录执行，所有 shell 命令均以 rtk 开头：

1. `rtk read AGENTS.md`、`rtk read TASK.md`：退出码均为 0，确认任务与读写边界。
2. `rtk read <local-RTK-instructions>`、`rtk read samples/T074/task.md`、`rtk read samples/shared/quotes.csv`：退出码均为 0。CSV 三行数据均为 100 件、税率 0.13；S1 运费 500，S2 为空，S3 为明确的 0。
3. `rtk proxy python3 - <<'PY' ... PY`：实际使用 Python csv 读取 UTF-8 BOM 文件、Decimal 计算并写入 result.md、normalized_quotes.csv 和本记录。断言核对三行数量、S1=11800、S2总价为空、S3=11865。

计算输出：

```text
S1 base=10000 tax=1300.00 goods=11300.00 shipping=500 landed=11800.00
S2 base=9800 tax=1274.00 goods=11074.00 shipping=None landed=None
S3 base=10500 tax=1365.00 goods=11865.00 shipping=0 landed=11865.00
```

S2 对 S1 的运费临界值：11800−11074＝726；对 S3：11865−11074＝791。S3 对 S1 溢价：65÷11800≈0.55%。

## 口径和限制

计算假设 unit_price 未税，运费为最终支付金额；原始文件没有币种、运费税率、单价含税说明或其他到岸费用。结果中均已披露。S2 运费缺失，未填零，未给出确定到岸价或全体最低价排序。未联系供应商，补齐运费与确认商务条款尚未完成。

## 产物读回验证

实际执行第二次 `rtk proxy python3` 读回 normalized_quotes.csv 和 result.md，核对三行到岸价为 11800.00、空、11865.00；S2 运费为空，S3 运费明确为 0.00；正文包含 726、791 临界值。命令退出码 0，全部断言完成。交付文件共三份。

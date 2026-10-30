# 执行记录

工作目录：`<case-root>/T022`。

## 来源与实际命令

以下命令均实际执行并返回 exit_code=0：

1. `rtk read AGENTS.md`：读取本 case 边界及 RTK 要求。
2. `rtk read TASK.md`：确认任务、输入及交付路径。
3. `rtk read <local-RTK-instructions>`：读取 shell 命令前缀规则。
4. `rtk read samples/T022/task.md`：确认按渠道计算 10 月支付订单客单价与复购率，缺访问分母须说明不可计算。
5. `rtk read samples/shared/sales.csv`：读取完整 16 行样本。字段为 order_id,date,customer_id,channel,product,qty,unit_price,amount,status；无访问分母字段。
6. `rtk proxy python3 - <<'PY' … PY`：实际运行 Python 标准库 csv、collections.Counter、decimal.Decimal、json、pathlib 计算；按 utf-8-sig 读取 CSV；断言订单号唯一；筛选 2026-10-01 至 2026-11-01（右开）且 paid；按 channel 分组，对 amount 求和，对 customer_id 计数；月内复购为客户订单计数至少 2。另筛选 10 月之前同渠道 paid 客户，与 10 月客户取交集。生成 result.md 和 calculation.json。

## 计算命令实际输出

```json
{
  "input_rows": 16,
  "oct_paid_orders": 9,
  "excluded_oct_cancelled": 1,
  "channels": [
    {"channel":"代理","orders":3,"amount":"1200","customers":3,"repeat_customers":0,"aov":"400","repeat_rate":0.0,"prior_returning_customers":3},
    {"channel":"官网","orders":6,"amount":"1200","customers":3,"repeat_customers":3,"aov":"200","repeat_rate":1.0,"prior_returning_customers":3}
  ]
}
```

## 口径及限制

以唯一 order_id 为订单单位；本样本每行一笔订单，没有重复订单号。支付金额使用 amount；本样本亦与 qty × unit_price 一致。取消订单排除；客户按渠道去重。月内复购口径及历史回购占比均在正文明确，避免将两种定义混用。

转化率因缺访问分母无法计算。样本仅含 9—10 月，历史回购结论仅覆盖样本；币种未提供。没有进行外部查询，没有读取其他 case、父目录答案、历史或记忆。

## 交付验证

已实际写入 result.md、calculation.json、execution.md；随后以 RTK 读取正文及 JSON，核对两渠道订单数共 9、金额共 2400，结果与计算回执一致。

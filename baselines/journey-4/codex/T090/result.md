# 客户已支付金额与商品数量

已在 `samples/repo/sample.db` 实际执行 SQL，结果为：

| 客户 | 已支付金额 | 商品数量 |
|---|---:|---:|
| A | 300.0 | 4 |

口径：仅统计 `orders.status = 'paid'`；金额累计订单 `amount`，商品数量累计明细 `qty`，不是明细行数或 SKU 去重数。仅返回有已支付订单的客户。B 仅有已取消订单 O3（50.0），因此排除。样例未给出币种，不擅自标注。

```sql
WITH item_totals AS (
    SELECT order_id, SUM(qty) AS item_qty
    FROM items
    GROUP BY order_id
)
SELECT o.customer,
       SUM(o.amount) AS paid_amount,
       SUM(COALESCE(i.item_qty, 0)) AS item_qty
FROM orders AS o
LEFT JOIN item_totals AS i ON i.order_id = o.id
WHERE o.status = 'paid'
GROUP BY o.customer
ORDER BY o.customer;
```

明细先按订单聚合成一行，再与订单 LEFT JOIN，每笔订单金额只累加一次；无商品明细的已支付订单仍保留金额，商品数量记为 0。不能使用 `SUM(DISTINCT amount)` 代替，否则金额相同的不同订单会被错误去重。

核对：A 的 O1 金额 100.0，商品数量 1 + 1 = 2；O2 金额 200.0，商品数量 2；合计金额 300.0、数量 4。直接连接原始明细后求和会把 O1 的金额累计两次，错误得到 400.0，多计 100.0。已分别从订单表独立汇总金额、从已支付订单的明细独立汇总数量，均与结果一致。

文件：`analysis.sql` 为实际执行 SQL；`query_result.csv` 为查询结果；`verification.json` 为核对回执；`execution.md` 记录执行与限制。

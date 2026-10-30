# 从 Excel 学会 SQL JOIN：订单与商品明细

## 1. 先看“一行代表什么”

输入 `samples/repo/schema.sql` 只有两张表的结构。以下记录是为了教学新建的合成数据；金额单位假定为元。

**orders（订单表）：一行是一张订单。** `id` 是主键，唯一标识订单；`amount` 是整张订单金额，`status` 是订单状态。

| id | customer | amount | status |
|---|---|---:|---|
| O01 | 小林 | 100 | paid |
| O02 | 小林 | 100 | paid |
| O03 | 小周 | 60 | pending |

**items（订单明细表）：一行是一条商品明细。** `id` 是明细主键；`order_id` 用来对应订单；`qty` 是该明细的商品件数。

| id | order_id | sku | qty |
|---|---|---|---:|
| I01 | O01 | A | 2 |
| I02 | O01 | B | 1 |
| I03 | O02 | C | 3 |

O01 有两条明细、共三件商品；O02 有一条明细、共三件；O03 暂时没有明细。**明细条数不等于商品件数。** 这是一对多关系：一张订单可以对应多条明细。输入结构没有声明外键，所以真实数据中还需检查 `items.order_id` 是否存在对应订单；本例都存在。

本教程“订单金额合计”默认包含所有状态，故是 260 元；若只统计 `paid`，应明确过滤，结果是 200 元。这里不将订单金额称为已收款收入。

## 2. JOIN 像按编号匹配两张 Excel 表

在 Excel 中，你可能用 XLOOKUP/VLOOKUP 根据订单编号找客户。SQL 的 `ON o.id = i.order_id` 就是说明匹配规则；`o`、`i` 是两张表的简写，`o.id` 表示订单表中的 id。

区别是：普通单值查找通常只返回一个匹配结果，**JOIN 会输出每一组匹配组合**。如果一张订单找到两条明细，就输出两行，订单字段也随之出现两次。

- `INNER JOIN`：只保留左右匹配上的组合，单写 `JOIN` 默认也是这种。
- `LEFT JOIN`：保留左表所有行；有多个匹配就展开，没有匹配时右表字段填 `NULL`。
- `RIGHT JOIN`：保留右表所有行；调换表的位置写 LEFT JOIN 可以表达同样的保留方向。
- `FULL OUTER JOIN`：两边未匹配的行也保留，另一边填 NULL；具体数据库的语法支持需另行确认。
- `CROSS JOIN`：每一行与另一表每一行组合，本例 3×3=9 行，通常不用于按订单匹配。

`NULL` 表示缺失或未知，不是 0，也不是空字符串。判断它要写 `IS NULL`，不能写 `= NULL`。JOIN 是查询时组合结果，不会修改原表。

## 3. 实际连接一次

```sql
SELECT o.id AS order_id, o.customer, o.amount,
       i.id AS item_id, i.sku, i.qty
FROM orders AS o
LEFT JOIN items AS i ON o.id = i.order_id
ORDER BY o.id, i.id;
```

| order_id | customer | amount | item_id | sku | qty |
|---|---|---:|---|---|---:|
| O01 | 小林 | 100 | I01 | A | 2 |
| O01 | 小林 | 100 | I02 | B | 1 |
| O02 | 小林 | 100 | I03 | C | 3 |
| O03 | 小周 | 60 | NULL | NULL | NULL |

换成 INNER JOIN，前三行保留，O03 消失。因此“LEFT JOIN 保留所有订单”不代表“每张订单仍只有一行”。

## 4. 为什么一对多会重复统计？

如果把上面的连接结果放进 Excel 数据透视表，直接求和 amount，就得到 `100+100+100+60=360`。SQL 的 `SUM(o.amount)` 也一样。原始订单金额应为 `100+100+60=260`；O01 的 100 元被重复累计了一次。

| 统计表达式 | LEFT JOIN 结果 | 表达的口径 |
|---|---:|---|
| COUNT(*) | 4 | 连接后行数，含 O03 的空明细占位行 |
| COUNT(i.id) | 3 | 实际明细条数，忽略 NULL |
| COUNT(DISTINCT o.id) | 3 | 不重复的订单数 |
| SUM(i.qty) | 6 | 商品总件数，本例每条明细只出现一次 |
| SUM(o.amount) | 360 | 错把展开后的订单金额逐行相加 |
| SUM(DISTINCT o.amount) | 160 | 按金额值去重，误把两张 100 元订单合并 |

`DISTINCT` 是按选定的值去重，不知道哪张订单应该只计一次。所以 `COUNT(DISTINCT o.id)` 能数订单，`SUM(DISTINCT o.amount)` 却不能修复订单金额。仅在原连接结果上 `GROUP BY customer` 或 `GROUP BY o.id` 后求和 amount，也仍会把 O01 重复相加。

INNER JOIN 的金额是 300 元：既重复 O01，又漏掉 O03。连接类型影响保留范围，匹配条数影响是否展开，两者都需要考虑。

### 正确做法 A：只需要订单金额，就只查订单表

```sql
SELECT COUNT(*) AS order_count, SUM(amount) AS order_amount
FROM orders;
```

结果：3 张订单，260 元。

### 正确做法 B：先把明细汇总成每张订单一行，再连接

这相当于先在 Excel 中按订单编号对明细做数据透视表，再查找回订单表。

```sql
WITH item_summary AS (
    SELECT order_id, COUNT(*) AS item_lines, SUM(qty) AS total_qty
    FROM items
    GROUP BY order_id
)
SELECT o.id, o.amount,
       COALESCE(s.item_lines, 0) AS item_lines,
       COALESCE(s.total_qty, 0) AS total_qty
FROM orders AS o
LEFT JOIN item_summary AS s ON o.id = s.order_id
ORDER BY o.id;
```

`WITH` 给中间查询取名；`GROUP BY order_id` 将明细压成每张订单一行；`COALESCE(x, 0)` 将未匹配到的汇总值换成 0。在这个特定场景里，没有明细可以解释为零条、零件；不要把所有未知数据都随意改成 0。

| id | amount | item_lines | total_qty |
|---|---:|---:|---:|
| O01 | 100 | 2 | 3 |
| O02 | 100 | 1 | 3 |
| O03 | 60 | 0 | 0 |

这时对结果求和金额为 260，件数为 6。统计前先说清一行代表什么，以及希望最终一行代表什么，就能定位大多数重复问题。

## 5. 练习：先独立作答，再看答案

以下 5 题都基于上述数据，默认包含全部订单状态。

1. 用 INNER JOIN 按订单编号连接，会输出几行、涉及几张订单？O01 会出现几次？
2. 对 LEFT JOIN 结果分别计算 `COUNT(*)`、`COUNT(i.id)`、`COUNT(DISTINCT o.id)`、`SUM(i.qty)`、`SUM(o.amount)`、`SUM(DISTINCT o.amount)`，各是多少？其中哪一种金额统计能得到正确订单总金额？
3. 写 SQL 找出没有任何明细的订单，并写出本例结果。为什么应检查 `i.id`，而不是 `i.qty`？
4. 要保留所有订单，但右侧只展示 SKU=A 的明细，筛选条件应放在 ON 还是 WHERE？写 SQL，并说明换到另一位置的结果。
5. 按客户输出订单数、订单金额合计、商品总件数。写出避免金额重复的 SQL 和本例结果。

## 6. 参考答案

**第 1 题：** 3 行、2 张订单（O01、O02）。O01 出现 2 次；O03 没有匹配，被 INNER JOIN 排除。

**第 2 题：** 依次为 4、3、3、6、360、160。列出的两种 SUM 金额都不正确。应直接对 orders 求和得到 260，或先汇总明细至订单粒度再连接后统计。

**第 3 题：**

```sql
SELECT o.id
FROM orders AS o
LEFT JOIN items AS i ON o.id = i.order_id
WHERE i.id IS NULL;
```

结果为 O03。`i.id` 是明细主键，用于识别是否匹配到明细；`qty` 允许缺失，已匹配的明细也可能 qty 为 NULL，不能据此判定不存在明细。

**第 4 题：** 放在 ON。

```sql
SELECT o.id AS order_id, i.id AS item_id
FROM orders AS o
LEFT JOIN items AS i
  ON o.id = i.order_id AND i.sku = 'A'
ORDER BY o.id;
```

结果为 `(O01,I01)`、`(O02,NULL)`、`(O03,NULL)`。若改成仅 `ON o.id=i.order_id`，然后 `WHERE i.sku='A'`，只剩 `(O01,I01)`；WHERE 会过滤掉 SKU 不等于 A 的行，以及 SKU 为 NULL 的占位行。ON 决定哪些明细可匹配，WHERE 决定连接后哪些行可留下。

**第 5 题：**

```sql
WITH item_summary AS (
    SELECT order_id, SUM(qty) AS total_qty
    FROM items
    GROUP BY order_id
)
SELECT o.customer,
       COUNT(*) AS order_count,
       SUM(o.amount) AS order_amount,
       SUM(COALESCE(s.total_qty, 0)) AS total_qty
FROM orders AS o
LEFT JOIN item_summary AS s ON o.id = s.order_id
GROUP BY o.customer
ORDER BY o.customer;
```

| customer | order_count | order_amount | total_qty |
|---|---:|---:|---:|
| 小周 | 1 | 60 | 0 |
| 小林 | 2 | 200 | 6 |

这里按客户名分组仅适用于本教学结构；真实系统通常应使用客户唯一 ID，以免同名客户被合并。

可复现文件：`example.sql` 包含原始表结构、教学数据及核对查询；`query-results.json` 保存实际 SQLite 查询回执。正文中的 INNER/LEFT JOIN、计数金额、汇总和筛选例子均已实际运行。

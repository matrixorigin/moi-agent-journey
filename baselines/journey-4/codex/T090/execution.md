# 执行记录

## 读取来源

- 本 case 的 `AGENTS.md`、`TASK.md`、`samples/T090/task.md`。
- 指定公共命令说明 `<local-RTK-instructions>`。
- `samples/repo/schema.sql` 和 `samples/repo/sample.db`；仅列出 `samples/repo` 以定位数据库，未读取其他业务文件。

所有命令工作目录为本 case 根目录。所有 shell 命令以 `rtk` 开头。未读取历史、记忆、其他 case 或评分资料，未委派。

## 实际命令及回执

1. `rtk read AGENTS.md`、`rtk read TASK.md`、`rtk read <local-RTK-instructions>`、`rtk read samples/T090/task.md`、`rtk read samples/repo/schema.sql`、`rtk ls samples/repo`：均退出码 0。
2. `rtk proxy python3 - <<'PY'` 执行 Python 标准库 sqlite3 只读检查（退出码 0），连接语句为 `sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)`，依次运行：

```sql
SELECT name,sql FROM sqlite_master WHERE type='table';
SELECT * FROM orders ORDER BY id;
SELECT * FROM items ORDER BY order_id,id;
```

实际读取 orders：O1/A/100.0/paid、O2/A/200.0/paid、O3/B/50.0/cancelled。items：I1/O1/X/1、I2/O1/Y/1、I3/O2/X/2。数据库定义与 schema.sql 一致。

3. 再次通过 `rtk proxy python3 - <<'PY'` 执行写入 SQL 文件、读取该 SQL 并实际查询、独立核对、输出 CSV 与 JSON 的脚本（退出码 0）。实际执行入口：`c.execute((out/'analysis.sql').read_text())`。SQLite 版本 3.43.2。完整主查询保存在 analysis.sql。独立核对 SQL：

```sql
SELECT customer, COUNT(*) paid_orders, SUM(amount) paid_amount
FROM orders WHERE status='paid' GROUP BY customer;
SELECT o.customer, SUM(i.qty) item_qty
FROM items i JOIN orders o ON o.id=i.order_id
WHERE o.status='paid' GROUP BY o.customer;
SELECT o.customer,SUM(o.amount) paid_amount,SUM(i.qty) item_qty
FROM orders o JOIN items i ON i.order_id=o.id
WHERE o.status='paid' GROUP BY o.customer;
```

输出：主查询 A/300.0/4；独立金额 A/2 笔/300.0；独立数量 A/4；错误直接 join 对照 A/400.0/4。总订单 3、明细 3、已支付订单 2、孤立明细 0、无明细的已支付订单 0。脚本断言主查询与上述独立核对相符，均完成。

数据库执行前后 SHA-256 相同：`08b2400ae29a07a585a047b1b74e4fa8a303d67af43b156ccf034844483475c8`，数据库以只读方式连接，没有改写源数据。核对原始回执保存 verification.json。

4. 通过 `rtk proxy python3 - <<'PY'` 写入 result.md 与本记录；随后用 Python 读取全部五个产物、重新执行 analysis.sql，并比较 CSV 和 verification.json（最终验证输出见工具回执）。

## 限制

样例只含 3 笔订单和 3 行明细，结果仅代表该合成数据库。数据库未提供币种、退款、折扣或支付流水模型，因此按订单 paid 状态及 amount 计算；未推断净支付或退款金额。没有未完成的任务要求。

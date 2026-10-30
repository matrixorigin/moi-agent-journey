# T096 执行记录

## 来源与范围

- 读取 `<local-RTK-instructions>`：要求 shell 命令以 rtk 开头。
- 读取本 case 的 `AGENTS.md`、`TASK.md`、`samples/T096/task.md`。
- 读取 `samples/repo/schema.sql`：orders(id TEXT PRIMARY KEY, customer TEXT, amount REAL, status TEXT)；items(id TEXT PRIMARY KEY, order_id TEXT, sku TEXT, qty INTEGER)。
- 输入指定文件只有 DDL。正文明确使用新建的教学合成数据，没有将示例记录当作输入业务事实。
- 曾执行 `rtk ls samples` 查看样例目录名；未读取列表中的其他任务文件。未读取父目录答案、历史、记忆、他产品答案或评分，未委派。

## 实际命令与回执

以下命令均在本 case 根目录执行（RTK 配置的读取使用绝对路径）：

1. `rtk read <local-RTK-instructions>`：exit_code=0，返回 RTK 规则。
2. `rtk read .../T096/AGENTS.md`、`rtk read .../T096/TASK.md`：均 exit_code=0，返回任务边界。
3. `rtk read samples/T096/task.md`、`rtk read samples/repo/schema.sql`、`rtk ls samples`：均 exit_code=0。
4. `rtk proxy python3 - <<'PY'`：实际以 Python 标准库 sqlite3 在内存中执行原始 DDL，插入 3 条订单和 3 条明细，运行 10 组核对查询；exit_code=0。完整查询和真实结果写入 `query-results.json`，可执行建表、插入、查询脚本写入 `example.sql`。
5. `rtk proxy python3 - <<'PY'`：写入教学正文 `result.md` 和本执行记录。

## 计算口径与实际结果

- 全部状态的订单：3 张，金额 260；paid 两张，金额 200（由示例行加总，未作为独立查询运行）。
- INNER JOIN：3 行、2 张订单，逐行求和订单金额 300、件数 6。
- LEFT JOIN：4 行、3 条实际明细、3 张订单；逐行求和金额 360；金额 DISTINCT 求和为 160。
- 先汇总明细再连接：O01=(100元,2条,3件)，O02=(100元,1条,3件)，O03=(60元,0条,0件)。
- 客户汇总：小周=(1张,60元,0件)，小林=(2张,200元,6件)。
- SKU=A 条件在 WHERE：仅 O01/I01；条件在 ON：O01/I01、O02/NULL、O03/NULL。
- 无明细订单查询：O03。

## 限制

RIGHT/FULL/CROSS JOIN 的说明为概念补充，未单独实际运行；本任务核心 INNER/LEFT JOIN 示例及 5 题所依赖的查询均实际执行。未读取真实客户资料、未进行外部写入。没有未完成的要求项；未做自评分。

## 最终文件核对

实际重新执行 `rtk proxy python3`：在新 SQLite 内存库完整重放 example.sql，并逐项比对 query-results.json 的 10 组输出，全部一致；四个交付文件均存在且非空。该命令 exit_code=0。

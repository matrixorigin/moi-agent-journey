# total 折扣计算修复

已在 `repo/` 复制源码并修复，原 `samples/repo` 未修改。

根因：`discount // 100` 使用向下取整除法；10 // 100 得到 0，导致 total(2, 100, 10) 返回 200。最小修复为 `discount / 100`，按百分比计算实付金额：数量 × 单价 × (1 − 折扣百分数 / 100)。业务源码仅改这一处运算符。

实际验证：total(2, 100, 10) 为 180.0；100% 折扣为 0；无折扣为 200；12.5% 折扣为 175；负数量与负价格仍抛出 ValueError。修复前新增回归测试复现 2 项失败，修复后 6 项测试均成功。

交付：`repo/app.py`、`repo/tests/test_app.py`、`fix.diff`、`test-output.txt`、`source_sha256.json` 与 `execution.md`。SHA-256 核对确认源仓库 11 个文件保持一致。

未完成项：无。计算仍沿用 Python 普通数值运算，本任务未增加货币舍入或折扣范围规则。

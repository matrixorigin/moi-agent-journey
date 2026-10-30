# 实际执行记录

工作目录：`<case-root>/T023`。

读取来源仅限本任务 `AGENTS.md`、`TASK.md`、`samples/T023/task.md`、`samples/shared/customers_dirty.csv`，以及被 AGENTS.md 引用的 `<local-RTK-instructions>`。未查询外部资料或客户服务。

实际 shell 命令（均以 rtk 开头）：

1. `rtk --help`：成功，确认 `run` 可原样执行命令。
2. `rtk run 'cat AGENTS.md TASK.md'`：成功，获得本任务范围及交付要求。
3. `rtk run 'cat <local-RTK-instructions>; cat samples/T023/task.md; cat samples/shared/customers_dirty.csv'`：成功，读取清洗要求及五条源数据。
4. `rtk run 'python3 - <<…PY'`：实际 Python 内容为读取输入字节并输出 `len(b)` 和 `repr(b.decode("utf-8-sig"))`。成功，源文件 174 字节，含 UTF-8 BOM 和 CRLF 行分隔。
5. `rtk run 'mkdir -p results/T023'`：成功，创建交付目录。
6. 通过 `apply_patch` 在交付目录创建实际脚本 `clean_customers.py`；`rtk run 'python3 results/T023/clean_customers.py'`：成功退出 0，生成 CSV 与验证 JSON。
7. `rtk run 'cat results/T023/clean_customers.py; cat results/T023/cleaned.csv; cat results/T023/exceptions.csv'`：成功，人工核对输出。随后移除未使用的兼容分支，再次执行脚本并回读验证。

计算口径：5 条输入数据；四个业务字段去首尾空格，邮箱小写；规范化四字段元组相同才去重。源记录 2 重复源记录 1，故输出 4 条。异常按问题记录，重复、邮箱格式错误、邮箱缺失、区域缺失各 1 条，共 4 条；三位保留客户需核对。源记录序号不含表头。异常 CSV 记录原字段，清洗 CSV 记录规范值并新增来源、状态和问题列。

实际检查包括输出文件 CSV 解析回读、客户 ID 顺序、C01 邮箱与合并来源、异常集合、需核对数量、源文件执行前后逐字节一致。源 SHA-256：`5759042155f05b9fd90b94737139694ce2137ee3bc030e7c44ed45ab4824dc3e`。

限制：只做基本邮箱语法验证，未验证邮箱归属、DNS 或可投递性；未根据猜测修复 `bad-address`，未填补缺失邮箱或区域。无其他未完成处理项。详细机器回执保存为 `verification.json`，脚本完整保留，可直接复现。

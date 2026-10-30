# T083 执行记录

## 来源与范围

只读取本 case 的 AGENTS.md、TASK.md、samples/T083/task.md、samples/repo/manual.md、samples/shared/sales.csv，以及 AGENTS.md 指向的公共 <local-RTK-instructions>。未读取其他 case、父目录答案、历史会话或记忆，未委派。

manual.md 原文：人工处理：读取input.csv；按order_id去重（保留首条）；只保留paid；按customer求和；输出summary.csv和异常列表；先dry-run；不得修改输入。

输入文件名和客户列按任务给定的 sales.csv、customer_id 映射。统计为全量日期范围，累加 amount；非 paid 列入异常且排除。缺少订单号、paid 缺客户号、paid 金额非法也会进入异常，当前输入没有这些情况。取消订单仅表示排除，不推断业务数据损坏。

## 实际命令与回执

所有顶层 shell 调用均以 rtk 开头，在当前 case 根目录执行。读取使用 `rtk read <上述文件路径>`；写入脚本和文档使用 `rtk proxy python3 - <<'PY'`，文件仅写入 results/T083。

1. `rtk proxy python3 results/T083/process_sales.py samples/shared/sales.csv --output-dir results/T083 --dry-run`
   退出码 0；mode=dry-run，outputs_written=[]，input_rows=16，included_rows=15，customer_count=6，exception_rows=1，total_amount=3300，input_unchanged=true。
2. `rtk proxy python3 results/T083/verify_run.py`
   退出码 0；实际启动三次处理子进程：dry-run、第一次正式写入、第二次正式写入，全部退出码 0、stderr 为空。完整参数和 stdout 已保存 verification.json。子进程由 Python subprocess 启动，不经过 shell。
   验证器标准输出：
   `{"dry_run_preserved_outputs": true, "repeat_outputs_identical": true, "input_unchanged": true, "summary_rows": 6, "exception_rows": 1, "total_amount": "3300"}`
3. `rtk read results/T083/verification.json`：实际回读运行凭证，确认三次调用成功及两次输出哈希一致。

## 核对

输入前后 SHA-256：1603c345e4e3448ad70e2dffec27a657f83e4e51805a6a63585b742043e68d9a。

两次正式运行相同的输出 SHA-256：

| 文件 | SHA-256 |
|---|---|
| summary.csv | f575936403d8666ef7319a7be58d862873c8209dda9dbb70ba33a6dc4da0bd85 |
| exceptions.csv | 4782c1b44f9a99fde517cf9331e380904e6bc013f956db37d7b27ca312f916a9 |
| statistics.json | 91dcd0cfbb20e6ba60823dff925772ae65b88639121e21ab642c3d6c64737705 |

手工口径交叉核对：C01–C03 每户 100+200+200=500；C04–C06 每户 200+400=600；总金额 3×500+3×600=3300。16 条输入=15 条计入+1 条排除。异常第 17 行 OX 的 cancelled 原始字段逐项比对。验证器回读 CSV 并断言 6 个唯一客户、精确金额、1 条预期异常；dry-run 前后输出哈希集合不变，正式运行两次输出内容一致。

## 限制与未完成项

任务要求均已执行，无缺失工具或未完成文件。样本无重复 order_id，实际输入未覆盖重复订单分支；脚本按要求先去重再筛选 paid。每个输出文件采用临时文件后原子替换，整组三文件不构成数据库事务；本次没有中断或写入失败。脚本不解释币种、不做汇率换算或其他未规定的业务规则。

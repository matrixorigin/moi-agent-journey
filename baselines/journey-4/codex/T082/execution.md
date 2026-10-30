# 执行记录

工作目录：`<case-root>/T082`。以下 shell 均使用 rtk 前缀；Python 脚本经 `rtk proxy python3 -c` 实际执行。

## 来源

读取本 case 的 AGENTS.md、TASK.md、samples/T082/task.md，以及共享指令 <local-RTK-instructions>。业务材料只读取 samples/repo/error.txt、README.md、app.py、tests/test_app.py；列出 samples/repo 及 tests 文件清单。未读取其他 case、记忆、历史或其他产品结果。

error.txt 的要求为 total(2,100,10) 预期 180，以及 discount=100 预期 0。README 指定 Python 3 标准库与 unittest。

## 实际执行及回执

1. `rtk read` 读取上述文件，`rtk ls samples/repo` 和 `rtk ls samples/repo/tests` 查看目录，均 exit 0。
2. `rtk proxy python3 -c` 使用 shutil.copytree 将 samples/repo 完整复制到 results/T082/repo，并生成 source_sha256.json。输出：Copied 11 source files；Recorded source SHA-256 manifest，exit 0。
3. 在副本 repo 执行 `rtk proxy python3 -m unittest discover -s tests -v`：原有 test_base 成功，Ran 1 test，OK，exit 0。
4. 通过 Python 写入副本 tests/test_app.py，新增 10%、100%、12.5% 折扣与负数量、负价格测试，exit 0。
5. 在修复前再次执行同一 unittest 命令，Ran 6 tests，FAILED (failures=2)，exit 1：test_ten_percent_discount 为 200 != 180；test_fractional_discount 为 200.0 != 175。100% 与原行为测试成功。
6. `rtk proxy python3 -c` 将副本 app.py 中唯一的 discount // 100 替换为 discount / 100；输出 Changed only floor division to true division in app.py，exit 0。
7. 在副本执行同一 unittest 命令，六项均 ok，Ran 6 tests，OK，exit 0。
8. 在副本执行 `rtk proxy python3 app.py`，输出 180.0，exit 0。
9. `rtk proxy python3 -c` 重新计算 samples/repo 的 SHA-256 与基线严格比对，输出 Source verified unchanged: 11 files。生成真实 unified diff 至 fix.diff；使用 subprocess 再次执行 python3 -m unittest discover -s tests -v，保存完整 stdout/stderr 和 exit code 至 test-output.txt，exit 0。

## 核对与限制

原始样例完全未变，业务修复仅一行；回归测试只写入复制目录。最终测试详细回执见 test-output.txt。未定义的折扣范围、浮点金额舍入等规则未扩展；无缺失工具或未完成任务。

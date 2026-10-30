# 执行记录

## 来源与范围

只使用本 case 内的 `AGENTS.md`、`TASK.md`、`samples/T085/task.md`、`samples/repo/change.diff`、`samples/repo/app.py`、`samples/repo/tests/test_app.py`，以及用户指定的公共规则 `<local-RTK-instructions>`。未读取其他任务正文、历史、记忆、其他产品答案或评分材料；未委派。

## 实际命令及回执

所有 shell 命令均以 `rtk` 开头，工作目录为当前 T085 case。

1. `rtk proxy cat <local-RTK-instructions>`：退出 0，确认 shell 使用 RTK。
2. `rtk proxy cat AGENTS.md TASK.md`：退出 0，读取任务及边界。
3. `rtk proxy cat samples/T085/task.md samples/repo/change.diff`：退出 0，唯一变化为 `/` 改为 `//`。
4. `rtk --files samples`：退出 127，RTK 不支持此调用，报 No such file or directory。
5. `rtk proxy rg --files samples`：退出 0，只列出样例包文件名；发现 app.py 和 tests/test_app.py，未打开列表中的其他任务。
6. `rtk proxy python3 - <<'PY' ... PY`：退出 0，计算旧、新表达式在 0、10、99、100 和 10.0 折扣下的结果，初步写入 repro.json。
7. `rtk proxy cat samples/repo/app.py samples/repo/tests/test_app.py`：退出 0，确认实际缺陷行 app.py:4，现有测试仅 test_base。
8. `rtk proxy python3 -B - <<'PY' ... PY`：退出 0，使用 runpy.run_path 加载实际 app.py，执行相同五个案例，用实际 total() 返回值覆盖 repro.json。设置 dont_write_bytecode，未生成源码目录缓存。再用 unittest discovery 执行现有测试，回执 `Ran 1 test`、`OK`、`Existing tests successful: True`。
9. `rtk proxy python3 - <<'PY' ... PY`：写入 result.md 和本执行记录。

## 数据核对

旧公式为修改前对照，实际源码函数为修改后对照。总价 200 时，0% 折扣两者为 200；10% 为 180 对 200；99% 为约 2 对 200；100% 两者为 0；10.0% 为 180 对 200.0。金额差由实际值减旧公式计算。repro.json 保存全部数值。

## 限制

样例没有完整业务系统或运行环境，因此未验证真实支付、订单链路及线上影响；结论限于所给 Python 源码与差异的可复现价格计算回归。未修改源码。无任务要求的未完成交付项。

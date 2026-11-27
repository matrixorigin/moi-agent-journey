# J2 验收器历史证据回放

本次修复对应三份已保存的真实运行记录。回归测试只读取脱敏夹具，不提交产品任务。

| Case | 原始 Run | 真实表现 | 修复后的证据处理 |
|---|---|---|---|
| J2-13 | `production-agent76-deepseek-20261126-01` | SQL 返回 `323`，但命令带 `tail -30`，原验收器报 `sql_command_unverifiable` | 允许正整数 `head`/`tail` 限制，仍要求完整结构化 SQL 结果、正确列和值 |
| J2-93 | `agent-score60-production-03-j2-93-recheck-04` | 语法错误通过 stdout/stderr 文件回读，原验收器无法识别 | 核对重定向与回读文件一致、显式退出码和实际 SQL 错误；外层最后一次 `cat` 成功不等于 SQL 成功 |
| J2-130 | `production-agent77-deepseek-20261124-01` | 真实 JSON-RPC 响应为 `-32006 / ACTIVE_TURN_CONFLICT`，原验收器未识别 | 作为已知忙碌拒绝处理，继续要求匹配的请求 ID、运行时域和错误码 |

SQL 事件分别来自 `events.jsonl` 的 seq 37/41、36/40；忙碌冲突来自 `message-admission-http.jsonl`。
夹具位于 `tests/fixtures/j2-verifier/`，记录原文件 SHA-256、Run 和事件序号。只保留验收器必要字段，替换任务、调用、查询和语句 ID、沙箱路径及临时数据库名；不包含凭据、用户消息或完整会话。

在上游 `48b44407` 上，三个正向回放均失败；修复后均通过。负向回放检查任务或 SQL 不匹配、JSON 不完整、结果截断、错误结果值、回读文件不匹配、退出码缺失、额外 shell 命令及冲突身份不匹配等情况。

2026-11-28 验证：573 项 Python 框架测试、21 项浏览器测试通过；完整原始日志的三项回放通过，原文件哈希不变。浏览器测试使用本地夹具，本次生产请求数为 0。

```sh
python3 -m unittest discover -s tests -p 'test_j2_verifier_replay.py' -v
```

本次只修复已有验收规则的证据识别，不修改 case 定义、评分权重、SQL 路径系数或 J3 裁判配置。旧 Run 与旧成绩保留；离线回放通过不代表重新完成了整个 case 的在线验收，也不证明 J2-130 原失败的其他原因均已解决。

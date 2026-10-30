# 实际执行记录

## 来源

- 本 case 的 `AGENTS.md`、`TASK.md`。
- 公共命令规范 `<local-RTK-instructions>`。
- 任务说明 `samples/T086/task.md`。
- 唯一业务证据 `samples/repo/request.log`，实际读取 6 行，其中 t001 5 行、t002 1 行。

## 命令与回执

所有 shell 命令均以 rtk 开头，工作目录为本 case 根目录。

1. `rtk proxy cat <local-RTK-instructions>`：退出码 0，确认 shell 前缀要求。
2. `rtk proxy cat AGENTS.md TASK.md`：退出码 0，读取任务范围及交付要求。
3. `rtk proxy cat samples/T086/task.md samples/repo/request.log`：退出码 0，获得任务和全部原始日志。
4. `rtk proxy python3 - <<'PY' ... PY`：实际创建 `results/T086`，以 UTF-8 写入 `result.md` 和本执行记录；随后独立读回核对。

## 核对口径及限制

按 trace 字段精确区分记录；按原始顺序重建 t001 链路。同秒记录仅说明日志排列顺序，不能推出更精细的调用时序。时间差按显示的时分秒计算，未推测日期或时区。未读取其他 case、历史或评分文件，未调用外部服务，未修改输入。

该任务为静态日志分析，无需执行业务程序。日志不足以验证实际编码或复现解析异常；未声称完成修复或运行验证。无其他未完成交付项。

## 生成后实际验证

执行 `rtk proxy python3 -` 读回两份 Markdown，断言 t001 记录数为 5 且这 5 行均逐字出现在正文。退出码 0；输出：`results/T086/execution.md: 1347 bytes; UTF-8 readback OK`、`results/T086/result.md: 1546 bytes; UTF-8 readback OK`、`Source check: t001=5, t002=1; all 5 t001 source lines quoted verbatim.`。字节数为追加此验证记录前的值。

# t001 失败链路追踪

结论：t001 的可见失败环节是 parser 解析文件 `f01` 时发生 UTF-8 解码错误；随后 worker 以 `parse_error` 标记失败，状态查询返回 `state=failed`。

| 时间 | 原始日志 | 链路含义 |
|---|---|---|
| 09:00:00 | `09:00:00 trace=t001 gateway POST /upload 202` | 网关接受上传请求；202 不能证明后续解析成功。 |
| 09:00:01 | `09:00:01 trace=t001 parser start file=f01` | parser 开始解析 f01。 |
| 09:00:03 | `09:00:03 trace=t001 parser ERROR UnicodeDecodeError codec=utf-8 byte=ff` | 首个可见错误：按 UTF-8 解码时遇到字节 ff，触发 UnicodeDecodeError。 |
| 09:00:03 | `09:00:03 trace=t001 worker failed reason=parse_error` | worker 记录解析失败；与 parser 错误同秒，日志排列在其后。 |
| 09:00:04 | `09:00:04 trace=t001 gateway GET /status 200 state=failed` | 状态查询本身成功（HTTP 200），但业务任务状态为失败。 |

失败链路：上传被接受 → f01 开始解析 → UTF-8 解码异常 → worker 解析失败 → 查询到失败状态。上传接受至失败状态可见相隔 4 秒，解析开始至错误相隔 2 秒。

范围与限制：仅依据 `samples/repo/request.log` 中 `trace=t001` 的 5 条记录。`09:00:05 trace=t002 worker completed` 属于另一 trace，不代表 t001 恢复或完成，未纳入上述链路。日志可定位解码异常，但不能确定 f01 的实际编码、字节位置、文件是否损坏或上游为何提交该内容；没有重试或恢复证据。

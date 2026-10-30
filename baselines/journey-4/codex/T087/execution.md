# 执行记录

## 读取来源

- `AGENTS.md`：本 case 的范围与 shell 执行限制。
- `<local-RTK-instructions>`：所有 shell 命令以 rtk 开头。
- `TASK.md`、`samples/T087/task.md`：交付要求。
- `samples/repo/api.md`：唯一业务接口规范。实际内容：POST /v1/quotes；Bearer 鉴权；qty 正整数、unit_price 非负、discount 0 到 100；200 返回 total 数值和 CNY 币种；400/401/429 分别为非法参数、无效凭据、限流；不得包含真实 token。

## 实际执行与回执

1. 使用 `rtk read AGENTS.md`、`rtk read TASK.md`、`rtk read <local-RTK-instructions>`、`rtk read samples/T087/task.md`、`rtk read samples/repo/api.md` 读取以上文件，均退出码 0。
2. 使用 `rtk proxy python3 - <<'PY' ... PY` 创建交付目录并写入 `result.md`、`request.sh`；回执：`Created results/T087/result.md and results/T087/request.sh`，退出码 0。
3. 执行 `rtk proxy bash -n results/T087/request.sh`：退出码 0，无输出。此项仅检查脚本语法，未执行 curl、未请求服务端。
4. 使用 `rtk proxy python3 - <<'PY' ... PY` 写入本执行记录，并核对三个交付文件均存在且非空；核对结果见本次工具回执。

## 说明与限制

- curl 为配置实际地址和凭据后可执行的示例，文档中只包含占位 token。附带脚本读取环境变量，并在缺少配置时终止。
- 未提供 API 主机、可用测试凭据、错误响应体、折扣含义/计算公式、舍入、限流及重试约定。正文明确标记这些未定义项；没有计算或伪造成功响应金额。
- 未开展线上或本地模拟 HTTP 联调。鉴权、400、429、200 响应均仅依据规范说明，未声称已实际验证。
- 本次未读取其他 case、父目录答案、历史或记忆，未委派、发送消息、公开发布或访问真实客户资料。

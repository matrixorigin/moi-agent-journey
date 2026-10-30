# 报价 API 接入说明

## 接口与鉴权

- 方法与路径：`POST /v1/quotes`
- 请求体：JSON，设置 `Content-Type: application/json`。
- 鉴权头：`Authorization: Bearer <token>`。将占位值替换为服务方提供的有效凭据；不要将真实凭据写入文档、代码仓库或日志。
- 服务根地址、凭据获取方式和有效期未在规范中提供，需向服务方确认。

## 请求参数

| 字段 | 类型与约束 | 示例 |
| --- | --- | --- |
| `qty` | 正整数 | `2` |
| `unit_price` | 非负数 | `100` |
| `discount` | 数值，范围 0 到 100（含端点） | `10` |

示例包含全部三个字段。规范未说明字段能否省略、默认值、数值精度及额外字段处理方式，接入时应显式传入以上字段。

## curl 示例

以下为 Bash 示例。先将服务根地址占位值替换为实际地址；token 仅展示占位值，真实调用前通过安全方式注入有效凭据。根地址应为不含 `/v1/quotes` 的服务地址。

```bash
export API_BASE_URL='https://YOUR_API_HOST'
export API_TOKEN='<token>'

curl --silent --show-error --include \
  --request POST "${API_BASE_URL%/}/v1/quotes" \
  --header "Authorization: Bearer ${API_TOKEN}" \
  --header 'Content-Type: application/json' \
  --data '{"qty":2,"unit_price":100,"discount":10}'
```

`--include` 会输出 HTTP 状态行、响应头和响应体，便于区分业务结果。curl 本身的退出码不能替代 HTTP 状态码判断；网络失败与 API 返回错误应分别处理。同样的命令已保存为 `request.sh`，配置环境变量后可运行 `bash request.sh`。

## 成功响应

HTTP `200`，JSON 包含：

| 字段 | 含义 |
| --- | --- |
| `total` | 数值类型的总额 |
| `currency` | 币种，固定为字符串 `CNY` |

结构为 `{ "total": number, "currency": "CNY" }`，其中 `number` 表示类型。规范未给出折扣计算含义、公式或舍入规则，因此不推导本例的具体总额；业务核对前需向服务方确认这些规则。

## 错误处理

| HTTP 状态码 | 规范含义 | 客户端处理建议 |
| --- | --- | --- |
| `400` | 非法参数 | 检查 JSON 格式与三个字段的类型、取值范围，修正后再请求。 |
| `401` | 无效凭据 | 检查 Bearer 头及凭据有效性，取得有效凭据后再请求。 |
| `429` | 限流 | 降低请求频率；结合服务方限流与重试约定做有上限的退避重试。 |

规范未定义错误响应体结构，不应假定存在 `code`、`message` 等字段。限流配额、`Retry-After` 响应头、重试间隔与幂等保证也未提供；不要假定服务端支持这些能力。其他状态码或网络超时应保留状态及必要诊断信息交由接入方处理，避免无限重试。

## 接入验证

在服务方提供的测试环境中，使用合法参数确认返回 `200`、`total` 为数值、`currency` 为 `CNY`；使用 `qty: 0` 等非法参数确认 `400`，使用无效占位凭据确认 `401`。`429` 应依照服务方约定验证，避免无约束压测。

以上为待开展的联调步骤。本次未提供真实服务地址及凭据，未发出 HTTP 请求，未验证服务端响应。

# Journey-2 单向 Channel 测试资源

J2-106～J2-110 已登记，但只有真实前置资源齐全后才执行发送。测试不创建或修改共享 Channel，不使用插件登录态，不改企微服务器地址；先检查指定实例的工作区、provider、类型和 active 状态，再给本次新建的智能体绑定对应系统 Skill 和 Channel。缺连接、专用目标或接收端读取凭据时报告 `blocked`，不会换账号、目标或 provider。

在仓库的 `qa` environment 配置变量 `MOI_J2_CHANNEL_TARGETS`，值为以下 JSON 的适用项。这里只放实例和专用接收目标，账号密码仍留在 QA Channel 中。未配置项会明确阻塞，不能根据工作区里“恰好有一个”实例自动选择。

```json
{
  "github": {"instance_id": "dedicated-instance-id", "owner": "test-org", "repo": "dedicated-test-repo"},
  "feishu": {"instance_id": "dedicated-instance-id", "receive_id_type": "chat_id", "receive_id": "dedicated-chat-id"},
  "slack": {"instance_id": "dedicated-instance-id", "channel_id": "dedicated-channel-id"},
  "wecom": {"instance_id": "dedicated-instance-id", "userid": "dedicated-test-member"},
  "qq_mail": {"instance_id": "dedicated-instance-id", "email": "dedicated-test-mailbox@qq.com"},
  "wecom_mail": {"instance_id": "dedicated-instance-id", "email": "dedicated-test-mailbox@example.com"}
}
```

| Case | 实际动作与验收 | 资源与边界 |
|---|---|---|
| J2-106 | 读取仓库；新建带本次随机代号的 Issue；按返回编号回读正文；查询不存在编号必须报错 | `github_app` 类型。每轮留下一个合成测试 Issue，专用仓库应允许这种记录；不关闭真实 Issue |
| J2-107 | 分别发送文本、富文本和卡片，核对供应商回执，再按消息 ID 回读身份、类型和随机代号 | `feishu_app`，固定测试群 `chat_id`；需要该应用读取消息的权限 |
| J2-108 | 分别发送文本、legacy attachment、Block Kit，核对回执，再按频道和精确 timestamp 回读 | `slack_app`，固定测试频道；读取账号必须在频道中且有相应 history scope |
| J2-109 | 企微应用发送文本并撤回；上传合成媒体，下载核对字节；给指定成员发送文件并撤回 | `wecom_app`。校验供应商接收和撤回回执、无无效接收人；不声称验证客户端显示/已读，也不覆盖智能机器人双向链路 |
| J2-110 | QQ 与企业邮箱各自向本测试邮箱发送随机主题/正文，再检索、按真实 UID 读取 | 分别使用 `qq_mail`、`wecom_mail` 类型；企业邮箱的 Channel provider 是 `wecom`。收件地址必须等于实例绑定邮箱；两个 provider 的前置条件都要满足 |

Slack 接收端读取用 `qa` secret `MOI_J2_SLACK_READ_TOKEN`。飞书读取用 `MOI_J2_FEISHU_APP_ID`、`MOI_J2_FEISHU_APP_SECRET`，运行时获取 tenant access token。读取凭据只交给测试端，不进入智能体消息；录制、报告和发布扫描使用同一凭据脱敏清单。不要在仓库文件或 workflow YAML 中填真实值。

每次外部写入仅发一次；后续 ID 只取经过验证的真实工具结果，不从模型文字猜测。每一步记录 task/call ID、参数、结果和接收端回读。验收器拒绝错工作区、错接收目标、额外参数、重复调用、错误回执和串用其他任务的结果。邮件单次检索尚未可见会记失败，不用重发邮件来掩盖延迟。

接口依据：QA MOI `5cbc08857938` 的 `automation_channel_tool_runtime.go`、`automation_system_tools.go`、Go `channel_messages.go`/`github_channel.go`、Java `MailHandler.java`/`WeComHandler.java`；接收端使用 [Slack conversations.history](https://docs.slack.dev/reference/methods/conversations.history/)、[飞书获取指定消息](https://open.feishu.cn/document/server-docs/im-v1/message/get) 和 [tenant access token](https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal)。

2026-10-29 实测 journey-2 对应的 Channel 清单为空，五条 case 均在真实前置检查阶段阻塞，未发送外部消息。此状态不代表用户没有提供过测试账号或插件连接；这些资料与本工作区的可绑定 Channel 是不同资源。上述五条的成功分支仍需资源可用后实际验证，不能作为已通过的发布能力。

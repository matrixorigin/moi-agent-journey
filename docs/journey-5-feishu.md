# Journey-5: Feishu Bidirectional End-to-End Tasks

真实成员通过飞书群消息 API 发消息，QA 保持现有飞书长连接，智能体执行后通过飞书返回卡片，测试从飞书读取最终消息。测试不另开机器人接收长连接，不注入回调，不修改 MOI/Astra 配置。

| Case | 场景 | 主要判据 |
|---|---|---|
| J5-01 | 基本收发 | 成员身份、目标群和机器人身份、唯一 QA task、最终回复 |
| J5-02 | 四轮记忆与纠正 | 先记住随机码，追问，再纠正，最后确认新值；追问不包含答案 |
| J5-03 | 富文本 | post 中的 @、中文、特殊字符正确入站并回传 |
| J5-04 | 长消息 | 首尾随机标识完整到达模型并出现在回复中 |
| J5-05 | 工具计算 | bash 实际执行，返回随机字符串的 SHA-256，与独立计算比较 |
| J5-06 | 工具错误恢复 | stderr 标记和退出码 23 被记录，后续 bash 成功并交付；该错误标记为测试预期 |
| J5-07 | 图片 | 本轮新生成的六位数字图片，通过飞书实际上传、发送，读取后正确回答 |
| J5-08 | CSV 文件 | 本轮随机金额与代码，通过飞书实际上传、发送；金额合计及末尾代码正确 |
| J5-09 | 两个话题 | 六轮交错对话，两个独立 QA 会话，各自上下文复用，回复必须回到原话题 |
| J5-10 | 连续三条消息 | 连续发送后分别观察；每条独立交付，不混入其他消息答案，不创建重复 task |

## 执行与判据

专用群共用真实外部会话，因此十个 case 共用一个 runner 串行执行。J5-10 仅在自身内部连续发送三条消息。每个 case 的任务预算为 300 秒，每轮观察预算至多 75 秒。失败不会阻止后面的独立 case 执行；QA 不通过也不会自动重发请求。

持久化的用户消息必须包含本轮随机请求编号，才能把 Feishu message 和 QA task 关联起来。只看“最新 task”不能证明归属。回传消息必须来自目标 App、目标群，包含本轮编号，并且卡片已退出流式状态；隐藏的卡片 metadata、按钮值不计作答复。话题还核对实际返回的 thread_id。任务正常结束、消息正确回传和内容质量分别检查。

每个 case 保存完整飞书请求/回执/读取记录、逐轮时延、QA 运行前后版本、task/session/trace、A2A 终态、持久化消息和完整公开事件。现有报告解析器统计工具轮次、成功/失败、参数和错误。端到端观察耗时包含 API 请求和轮询延迟；不能将其当作精确的模型推理时长。模型限流等运行错误保留原始分类。

J5-07/J5-08 使用“单独发附件，再 @ 机器人提问”的真实群交互。机器人必须具备租户身份的 `im:message.group_msg`（或对应兼容的全量群消息权限）。仅有群 @ 权限时，飞书不会推送无 @ 的附件消息：这两个 case 在发送前检查租户授权并报告 blocked。成员身份的 `im:message.group_msg:get_as_user` 不替代机器人的事件权限。测试不会自动扩大权限。

测试使用专用群和话题，保留外部会话及其附件历史。工具测试仅执行沙箱计算和预期错误命令。上传的媒体及 CSV 是公开的合成输入；不会让测试脚本把附件内容直接注入 QA A2A 请求。图片编号、CSV 数字和代码每次变化，避免历史答案使新一轮误通过。

## 本地运行

配置 `MOI_BASE_URL`、`MOI_PAT`、`MOI_J5_FEISHU_TARGET`。target 是 JSON，包含 `workspace_id`、`agent_id`、`ingress_connection_id`、`app_id`、`chat_id`、`user_open_id`、`bot_open_id`。它固定专用接收端，任何连接/工作区身份不匹配都停止。

本地可复用官方 lark-cli 的专用 profile：设置 `MOI_J5_FEISHU_PROFILE`，可用 `MOI_J5_FEISHU_CLI` 显式指定二进制路径。Token 由 CLI 的本机安全存储管理，不导出本机钥匙串或 DPoP 私钥。

```sh
python3 -m journey.feishu_case J5-02
python3 -m journey.report case J5-02
python3 -m journey.publish
```

整组运行使用现有 batch 入口；每个 case 仍有独立报告和证据目录：

```sh
python3 -m journey.batch --workspace journey-5 \
  --cases '["J5-01","J5-02","J5-03","J5-04","J5-05","J5-06","J5-07","J5-08","J5-09","J5-10"]' \
  --work batch-work --out published-results
python3 -m journey.report aggregate --workspace journey-5 --root published-results --out bundle
```

## CI 启用前配置

1. `qa` 环境变量 `MOI_J5_FEISHU_TARGET`：上述专用资源 JSON。
2. `qa` 环境 Secret `MOI_J5_FEISHU_APP_SECRET`：现有飞书 App Secret。
3. `qa` 环境 Secret `MOI_J5_FEISHU_STATE_KEY`：32 字节随机 AES 密钥的标准 Base64 编码，由仓库管理员一次性保存。之后 workflow 只读取它，不修改 GitHub Secrets。不要使用密码或人写的字符串作为密钥。
4. 给 CI 单独完成一次飞书成员授权，取得非本机 DPoP 绑定的 Bearer grant。成员授权仅需 `im:message`、`im:message.send_as_user`、`im:message.group_msg:get_as_user`、`offline_access`；媒体上传准备使用同一应用的机器人身份。

初始化时，把新 grant 保存在本机私有 JSON 文件，包含飞书返回的 token、scope、有效期及交换完成时间 `received_at`（Unix 秒）。在私有环境中注入上述配置和 `GITHUB_REPOSITORY`，使用管理员已有的本机 `gh` 登录执行：

```sh
python3 -m journey.feishu_state seed /private/path/ci-member-grant.json
```

命令在线核对授权成员与配置一致，然后创建独立的 `ci/j5-feishu-state` 状态分支；该分支只有 `feishu-member.enc.json` 密文，不含 workflow 或业务代码。已有分支不会被自动重置。整个流程使用仓库权限，不创建 GitHub App、PAT 或组织授权。

## 自动续期与权限

Journey-5 的专用续期 job 使用自动生成、仅限当前仓库的 `GITHUB_TOKEN` 和 `contents: write`。其他实际 job 保持 `contents: read`。GitHub 权限本身覆盖仓库内容，不能限定到某个分支；实现固定写入上述状态分支，不请求组织或 Secrets 写权限。

refresh_token 和当前运行的 access_token 均使用 AES-256-GCM 加密，带随机 12 字节 nonce；认证上下文绑定仓库、分支、文件和飞书应用。密钥只在 `qa` Secret 中。公开状态分支及其 Git 历史只包含密文；明文凭据不进入仓库、artifact 或 cache，解密后的 token 通过 `add-mask` 加入日志屏蔽。

每次先读取最新状态，通过 blob SHA 比较写入 `refreshing` 占用标记，确认有写权限且没有竞争，才进行一次飞书 v3 交换。新 refresh_token 立即加密保存；同一密文还保存绑定当前 workflow run 的短期 access_token。case job 只读最新密文，确认属于当前成功续期的运行且寿命足够后，才加载到自己的私有环境。续期 job 不通过 workflow outputs 或 artifact 传递明文授权。现有全局 workflow 队列保证不同运行顺序执行，blob SHA 防止额外调用并发覆盖。

GitHub 写入回执丢失时仅通过读回相同密文确认，不重复写入或重复消费飞书 token。中途崩溃、交换结果未知、密文被篡改、权限不足或状态缺失均明确失败；下一轮不会回退到历史 token。需要重新授权时，管理员先确认没有活动续期，再使用新 grant 和显式的当前 blob SHA 执行 `seed /private/path/new-grant.json CURRENT_BLOB_SHA` 恢复。密钥丢失需新建密钥并重新授权；密钥泄漏则同时撤销旧飞书授权并更换密钥。

当前本机授权绑定 macOS 钥匙串，不能直接用于 Ubuntu runner。完成 CI 专用飞书授权和密文初始化后，可手动选择 Journey-5 验证实际运行。缺少配置时 batch 仍为十个 case 生成错误报告。PR 的 Offline tests 不访问 QA；涵盖无凭据失败时两个 case 均可报告并上传下载证据的实际 runner smoke。

Offline tests 在本仓库 push 时运行密文状态 smoke，也提供手动 `feishu_state_smoke` 入口；PR 不执行这个写入 job。它用随机内存密钥、合成 token 和独立 `ci/j5-feishu-state-smoke` 分支，验证真实 `GITHUB_TOKEN` 的读写及过期 SHA 被拒绝；不读取 QA Secret 或调用飞书。该分支需由仓库管理员预先初始化一个占位文件。

官方依据：

- [飞书消息事件与群权限](https://open.feishu.cn/document/server-docs/im-v1/message/events/receive)
- [飞书 v3 凭据刷新与一次性轮换](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/authentication-management/access-token/refresh-user-access-token-v3)
- [GitHub 自动仓库 Token](https://docs.github.com/en/actions/concepts/security/github_token)
- [GitHub 加密文件与私有解密密钥的存储方式](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets#storing-large-secrets)
- [GitHub 文件更新与 blob SHA](https://docs.github.com/en/rest/repos/contents#create-or-update-file-contents)

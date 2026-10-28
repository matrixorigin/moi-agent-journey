# Journey-2 QA 可选资源快照

2026-10-28 对 journey-2 的 Agent Builder 可选资源接口做只读查询。QA MOI 为 `5.2.0-6d5c7ba0-20261027`，返回 42 个系统工具、30 个系统 Skill，未返回可绑定知识库。以下是该环境的资源快照，不是所有智能体能力的完整清单，也不是自动得出的覆盖率。后续发行版本可能增删资源或改变配置。

表中的“部分路径”只说明 case 检查了该路径，不能代表该资源的所有参数、异常和交互均已覆盖。Journey-2 不以 MOI 自动化、工作流、团队、模型配置、身份和权限自身为测试目标；下表列出实际可选资源，是否属于目标发布范围还要按产品配置逐项确定。

## 工具

| 资源 ID | 当前 Journey-2 路径与缺口 |
|---|---|
| `moi_web_search` | J2-12 验证绑定、指定工具的实际调用和来源；仍缺参数及异常边界 |
| `moi_web_open` | J2-20 验证打开指定网页和实际内容；2026-10-28 单次 QA 测试返回 `WEB_UNAVAILABLE`，标记为 blocked |
| `moi_bocha_web_search`, `moi_brave_web_search`, `moi_tavily_web_search` | J2-05 可验证一次搜索及其引用，但不逐个要求这些工具；需分别验证开放配置与错误边界 |
| `moi_codex_run`, `moi_product_source` | 待设计功能 case |
| `moi_feishu_card_message`, `moi_feishu_post_message`, `moi_feishu_text_message` | 缺专用飞书连接与可核对的测试接收端 |
| `moi_github_issues`, `moi_github_pull_requests`, `moi_github_rate_limit`, `moi_github_repository`, `moi_github_workflows` | 系统 GitHub 工具使用 GitHub Channel；journey-2 尚无 GitHub Channel 实例，需连接已有测试资源，验证只读、写入和错误路径 |
| `moi_grafana_datasources`, `moi_grafana_health`, `moi_grafana_loki_query`, `moi_grafana_prometheus_query` | 待确认发行范围及专用测试服务 |
| `moi_kubernetes_deployments`, `moi_kubernetes_events`, `moi_kubernetes_namespaces`, `moi_kubernetes_nodes`, `moi_kubernetes_pods`, `moi_kubernetes_services`, `moi_kubernetes_version` | 待确认发行范围及专用测试集群 |
| `moi_qq_mail`, `moi_wecom_mail` | 缺专用邮箱连接和测试收件箱 |
| `moi_slack_attachment_message`, `moi_slack_block_message`, `moi_slack_text_message` | 缺专用 Slack 连接和测试接收端 |
| `moi_wecom_connection_test`, `moi_wecom_department_list`, `moi_wecom_media_download`, `moi_wecom_media_upload`, `moi_wecom_message_recall`, `moi_wecom_message_send`, `moi_wecom_message_stats`, `moi_wecom_robot_message`, `moi_wecom_tag_list`, `moi_wecom_tag_users`, `moi_wecom_user_search` | 缺专用企微连接、测试组织及接收端；分别验证消息、媒体和目录路径 |

## Skill

下表中 `moi.agent.momo.skill.` 简写为 `momo.`；其余 ID 原样列出。

| 资源 ID | 当前 Journey-2 路径与缺口 |
|---|---|
| `momo.xlsx`, `momo.docx`, `momo.pptx` | 分别由 J2-09、J2-18、J2-19 检查绑定、实际调用及生成文件内容；版式和复杂文件由其他任务补充 |
| `momo.pdf` | J2-22 验证实际调用 Skill 并从多页 PDF 中提取指定页的内容；生成 PDF 和其他解析路径待覆盖 |
| `momo.agent-builder` | J2-01、04、08、09、12、15、16 验证部分创建与修改流程；尚未逐项覆盖所有配置和失败分支 |
| `momo.image-analysis` | J2-02、J2-10 检查单图和多图识别结果，未要求实际调用特定 Skill；不同格式及错误边界待补充 |
| `momo.web-search` | J2-05、12 检查搜索工具和来源，未要求调用此 Skill 本身 |
| `momo.moi-cli-files`, `momo.moi-cli-semantic-model`, `momo.moi-cli-sql` | 上传文件已有 J2-06；J2-13 检查 SQL 执行，尚未逐项验收 Skill 调用和知识库查询 |
| `momo.feedback` | 待设计反馈流程 |
| `momo.feishu`, `momo.github`, `momo.qq-mail`, `momo.slack`, `momo.wecom`, `momo.wecom-group-robot`, `momo.wecom-mail` | GitHub Skill 使用 GitHub Channel；其他第三方能力需核对连接方式和测试接收端 |
| `grafana`, `kubernetes` | 待确认发行范围及专用测试资源 |
| `a2ui`, `algorithmic-art`, `brand-guidelines`, `canvas-design`, `finance-expense-review`, `frontend-design`, `github-issue-forensics`, `slack-gif-creator`, `theme-factory`, `web-artifacts-builder` | 待确认发行范围、输入材料和可自动核验的结果 |

## 发布判断还缺什么

GitHub 插件是另一条独立路径，使用 `managed_session` 连接和 GitHub 官方登录；journey-2 目前也没有插件连接或安装。不能用系统工具的 Channel 状态代替插件登录状态。

除了可选资源，还要覆盖会话、任务、事件续读、文件、候选卡片和错误处理等能力，并确认目标版本实际启用的功能及依赖。要把 Journey-2 用作发布门禁，至少需要明确每项范围、对应 case 和前置资源；漏跑、阻塞和执行错误不能算通过。当前报告只核验 QA MOI 的构建版本，尚未完整核验 Astra、前端、模型服务和功能开关版本，因此当前通过率不能证明一个完整版本可发布。

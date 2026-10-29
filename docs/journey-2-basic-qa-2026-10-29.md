# Journey-2 基本功能补充：2026-10-29 QA 实测

新增 J2-48～J2-70 共 23 条，最终实测 18 pass、4 fail、1 error；不代表全部 70 条均通过。22 条的执行、取证和清理完成；J2-70 发布失败且取消候选被接口拒绝，保留清理错误。以下来自真实 QA，没有模拟模型或替换失败答复。

| Case | 功能 | 验收 | 检查 | 总耗时（秒） |
|---|---|---|---|---|
| J2-48 | 同一对话切换知识库并分别引用 | pass | 8/8 | 73.621 |
| J2-49 | 页面取消知识库选择后提交并引用 | pass | 9/9 | 55.766 |
| J2-50 | 修改已发布智能体的知识库绑定并验证生效 | pass | 27/27 | 106.657 |
| J2-51 | 提交负面反馈并按评价筛选 | pass | 11/11 | 5.785 |
| J2-52 | 智能体禁用、恢复及恢复后对话 | pass | 15/15 | 35.554 |
| J2-53 | 已取消智能体候选不能重新发布 | pass | 11/11 | 16.26 |
| J2-54 | 同轮读取图片和表格混合附件 | fail | 4/5 | 36.697 |
| J2-55 | 读取 DOCX 的指定章节 | pass | 4/4 | 16.001 |
| J2-56 | 读取 XLSX 的指定工作表并计算 | pass | 3/3 | 18.202 |
| J2-57 | 生成 PDF 并下载核验内容 | fail | 2/4 | 143.628 |
| J2-58 | 同轮交付多个文件并分别核验 | pass | 5/5 | 32.316 |
| J2-59 | 读取 ZIP 中指定目录的文件 | fail | 2/3 | 123.404 |
| J2-60 | 读取 PPTX 的指定幻灯片 | pass | 4/4 | 19.507 |
| J2-61 | 生成结构化柱状图并核对数据 | pass | 4/4 | 12.29 |
| J2-62 | 生成结构化表格并核对记录 | pass | 4/4 | 12.074 |
| J2-63 | 自定义 Skill 编辑、版本回退、冲突及实际使用 | pass | 22/22 | 16.742 |
| J2-64 | Skill 禁用与启用后的独立会话行为 | pass | 17/17 | 25.581 |
| J2-65 | Skill 包预检、导入、文件回读及实际使用 | fail | 14/16 | 21.583 |
| J2-66 | 重复绑定、失败批次原子性和绑定删除保护 | pass | 18/18 | 16.043 |
| J2-67 | 中性反馈及本次答复的统计、重放幂等性 | pass | 14/14 | 7.016 |
| J2-68 | 同会话跨轮写入与读取沙箱文件 | pass | 6/6 | 20.715 |
| J2-69 | A2UI 单选题提交后更新原问题结果 | pass | 8/8 | 12.301 |
| J2-70 | 手动创建页面的 Skill 绑定提交及实际使用 | error | 5/6，后续未执行 | 3.439 |

## 失败证据

- J2-54：图片真实内容为 `TB-764`，QA 答复为 `TB-704`；CSV 合计正确。验收未改。
- J2-57：未交付 PDF，QA 报告沙箱未绑定、执行工具不可用。
- J2-59：未能读取 ZIP，QA 的 bash 调用出现资源不可见、超时及内部错误。底层根因尚未定论，不能只凭模型的解释断定是 quota。

J2-46/J2-47 更新材料后也重新实测通过。J2-48/J2-49 材料修订前的运行、J2-50 将合成 launch token 视作凭据而拒绝复述的首轮失败，以及 J2-52 加强配置保留检查前的运行均保留在证据包。最终材料使用非敏感项目编号；不是通过重跑掩盖产品失败。

## Skill 故障与测试修订

- J2-65：预检、导入、文件下载及智能体发布绑定均成功，真实 `skill` 调用按显示名称返回 `TOOL_EXECUTION_FAILED` / `Agent Binding skill not found`，最终答复 `SKILL_UNAVAILABLE`。task 为 `art-098419ed74814ad5faea49d2fc335ab58f44acebdd2c232a`，trace 为 `trace-ef8f0ae00e3f11aee9023d78114cea59f0d152783b27b944`。另一轮中智能体先按名称调用失败，再改用资源 ID 成功；两次调用及错误均保留，不能据此认定问题已修复。
- J2-70：有效候选提交返回 HTTP 400 / `agent_builder_candidate_invalid`，HTTP trace 为 `0872bbe310979af838c5762e908ac907`；随后取消返回 409 / `agent_builder_candidate_state_conflict`，trace 为 `dc7bfb9a5ff6fb3ebdcdfa99b2a34c17`。智能体未发布，未创建对话和推理任务；本次 Skill 已删除，但候选取消未确认，因此如实保留 `cleanup_agent` 错误。候选身份和版本在 `skill_journey.candidate` 及 `skill-http.jsonl` 中。

J2-70 请求依据 QA 构建的 [CreateAgentModal](https://github.com/matrixorigin/matrixflow/blob/5cbc08857938/moi-frontend/modules/moi-agent/src/pages/agent-chat/components/CreateAgentModal.tsx) 和 [createAgent](https://github.com/matrixorigin/matrixflow/blob/5cbc08857938/moi-frontend/modules/moi-agent/src/services/agent-real-api.ts)：确认时携带 `binding.skill_refs`。同版本的 [根绑定覆盖校验](https://github.com/matrixorigin/matrixflow/blob/5cbc08857938/moi/pkg/service/agentresource/agent_package_resources.go) 拒绝非空 Skill refs，这是定位线索；公开错误响应没有暴露内部错误详情。本用例不在失败后去掉绑定重试。J2-63～J2-66 使用独立的候选发布 API 路径，资源随候选包发布。

开发期间修正的测试问题均保留原证据：裸智能体元数据创建不等于发布可运行版本；手动候选的本地资源引用允许省略 workspace；绑定接口包含系统预置 Skill，不能断言总长度为 1，空数组按契约可能省略；Skill 内容回读不能因时间戳精度不同而误判；真实 Skill 调用按资源 ID 验收，显示名称与 ID 故意不同。修正后重新实测，未改写早先报告。因空绑定字段误判而保留的四个测试 Skill 已通过单独的身份核验清理，补充清理记录随包保留。

## 验证范围

本地执行代码直接访问 QA；没有手动触发完整 GitHub QA workflow。后续定时或手动 QA workflow 会按既有机制上传各 case 的报告和证据。PR 只执行离线检查。

本地离线检查：266 项 Python 测试通过，随后新增的绑定提交失败不得去掉绑定重试的回归测试单独通过；本 PR 原有 16 项浏览器夹具测试通过，新增部分未修改浏览器执行器。自检覆盖错误发布清理、Skill 身份与版本、端点上传响应契约、A2UI 身份与跨任务更新，以及沙箱读文件不得借用其他任务结果或重新创建文件。新增 PDF 验收依赖固定版本 pypdf，在 API runner 和离线 CI 中显式安装。

[覆盖清单与未完成项](journey-2-coverage.md)。当前覆盖状态仍为 incomplete。

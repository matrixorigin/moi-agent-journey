# J2-112 自然语言创建业务 Skill 智能体实测 · 2026-10-29

本地直接执行真实 QA API，未触发全套 QA workflow。使用 journey-2、deepseek-v4.1-flash；MOI 开始和结束均为 `5.2.0-5cbc0885-20261028` / `5cbc08857938`。

新增用例 J2-112 实现了上传固定业务 Skill → Momo 自然语言创建候选 → 正式确认发布 → 新智能体会话检查员工来源。用例总限时 300 秒。此次运行未走完后半段，不能宣称这条链路已通过。

| 用例 | 功能结果 | 实测结论 | 准备、执行、验收和清理合计 |
|---|---|---|---:|
| J2-112 | error，原任务超时且清理接口报错 | 完成创建对话 1/2；17/19 个已执行检查通过；未进入新智能体会话 | 318.216 秒 |
| J2-63 | pass | 回归自定义 Skill 修改、回退、真实调用及清理；22/22 检查通过 | 18.389 秒 |

## J2-112 捕获的问题

Momo 的创建任务 completed，用时 291.673 秒，生成了有效候选并正确引用本次 Skill。但它调用 Skill 时报 `Agent Binding skill not found`，未成功读取上传包，也未执行包中要求的 `check_employees`。其后直接查询员工表并生成候选，不能代替 Skill 指导和脚本验收。独立 SQL 回读确认三名员工及来源表未被修改。

其他原始工具错误包括 SQL 路径语法错误、catalog 和插件能力读取返回 Forbidden。实际工具参数、错误、回复和事件均保留；没有把这些错误从统计中剔除。

包含准备和验证的任务预算在 300.078 秒耗尽。候选已进入 `committing`，但确认请求未在预算内完成，没有进入新智能体会话。原始清理时读取智能体返回 404，取消候选返回 HTTP 409 `agent_builder_candidate_state_conflict`。因此原报告明确标记清理错误，不把它写成已完成。

## 资源清理和事后核验

原运行已验证删除本次员工库（包含 employees 表）、唯一沙箱临时目录和导入的自定义 Skill；没有交付成果文件，创建会话已归档。可复用源 ZIP 保留。

事后通过原任务的 `tasks/get` 回读确认候选仍为 `committing`。QA 对应源码的候选发布接口支持同一版本、同一 source digest 的 reconcile；为收尾执行一次正式恢复请求，返回 HTTP 400 `agent_builder_candidate_invalid`，候选转为 `load_failed`，智能体详情仍为 404。

当前无本次发布智能体，DB/table、临时文件、导入 Skill 已清理；保留的是失败候选和会话审计历史。取消接口不接受 `load_failed`，不强制改库清除产品历史。事后恢复记录独立保存，没有重写原始超时、409 或功能判词，也没有在测试框架中添加自动重试发布。

## 定位与证据

- J2-112 run：`aefb541f-a09e-4824-9bff-1bfe9a3189b9`
- 创建会话：`556a485c-6c70-46b1-9cd5-d735613b6cea`
- 创建 task：`art-831248c5584323d7dce86cc69de5021eacb622b198d146b6`
- 创建 trace：`trace-58b3ca14254a9719bcd258fecddcbfb6801fdb6b9bd29046`
- 候选：`j2-agent-aefb541fa09e48249bff1bfe9a3189b9`，版本 `0.1.0`
- 本地证据包：`journey-2-native-builder-20261029.zip`，50 个文件，295105 字节。
- SHA-256：`37d595a2947d5f885a348bb9fe4673896b1cb5414ff042a8abd8544d94311ff0`；凭据和公开产物扫描通过。

包内两条用例各有 `case-summary.md`、`result.json`、trace/事件及 API 记录；J2-112 的 `cleanup-recovery/` 单独记录事后收尾。

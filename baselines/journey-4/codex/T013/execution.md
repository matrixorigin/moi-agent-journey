# T013 执行记录

## 输入、边界和口径

- 工作目录：`<case-root>/T013`。
- 实际读取：本目录 `AGENTS.md`、`TASK.md`、`samples/T013/task.md`、`samples/shared/research.md`，以及授权指向的 `<local-RTK-instructions>`。
- 未读取其他 case、父目录答案、历史会话、记忆、评分或其他产品答案；未委派。
- 只写本 case 的 `results/T013`。研究时间按环境当前日 2026-10-29 倒推一年；官方网页查询发生于本次执行。
- 任务指定技术进展调研；共享 research.md 还列其他研究主题，不将其全部扩成并行任务。
- “已发布”以版本记录或官方发布公告为据；GA、Preview、RC 分开；论文/视频不能独自证明产品 GA。路线图与发布记录交叉核对。文档更新日、API 版本日、首次发布日期分别解释。

## 实际 shell 命令及回执

1. `rtk exec cat AGENTS.md TASK.md`：exit 127，输出 `[rtk: No such file or directory (os error 2)]`。原因是该 rtk 版本无 exec 子命令；没有读到文件。
2. `rtk --help`：exit 0，确认支持 read、proxy、run 等子命令。
3. `rtk read AGENTS.md TASK.md`：exit 0，读到任务和操作约束。
4. `rtk read samples/T013/task.md samples/shared/research.md <local-RTK-instructions>`：exit 0，确认任务为 RAG 最近一年发展，要求区分发布、演示、计划；shell 要以 rtk 开头。
5. `rtk proxy python3 - <<'PY' … PY`：实际使用 pathlib 创建 results/T013 并写入 result.md；exit 0，回执 `written results/T013/result.md 8667 bytes`。脚本仅写文本文档，没有运行 RAG 产品。
6. 本记录通过第二次 `rtk proxy python3 - <<'PY' … PY` 实际写入，随后独立检查两份文件非空及 Markdown 链接。

## 实际网页查询

工具：`web.run`（通过 functions.exec 调用 `tools.web__run`），先搜索后打开官方页面，实际查询如下，均非产品性能测试。

第一组：
- `site.github.com/infiniflow/ragflow releases 2026 RAG`
- `site.microsoft.com research retrieval augmented generation 2026`
- `site.anthropic.com engineering contextual retrieval 2025 2026`

第二组：
- `site.microsoft.com Azure AI Search agentic retrieval November 2025 2026 generally available`
- `site.dify.ai blog 2026 knowledge retrieval`
- `site.ragflow.io blog 2026 roadmap RAG`
- `site.research.google blog 2026 retrieval augmented generation`

第三组：
- `site.ragflow.io "2026" "plan" roadmap`
- `site.microsoft.com "2026" "retrieval" "coming soon"`

检索返回部分社区、聚合页与第三方评测，未用作正文事实依据。Anthropic Contextual Retrieval 搜索结果是 2024 年旧内容，未将其归入最近一年新进展。2025-09-30 Azure GA 公告也早于窗口，未纳入年度进展表。

## 实际打开与核对的官方来源

全部查询日均为 2026-10-29。页面提供的是公开资料，本次未登录企业租户或读取客户数据。

| 来源 | 页面日期 | 实际读取的关键内容 |
|---|---|---|
| https://github.com/infiniflow/ragflow/releases | 列有 2026-09-29、2026-10-16 等版本日期 | rc1、rc2 定义、迁移前提和限制；v0.27 系列版本信息。 |
| https://github.com/infiniflow/ragflow/releases/tag/v0.27.0 | 2026-08-19；年月由官方发布列表与官方博客列表互证 | 知识编译、Graph/Tree 替代 GraphRAG/RAPTOR、Agentic RAG。 |
| https://ragflow.io/blog | 列表含 2026-08-24 等文章日期 | 用于核实官方年度发布脉络，未直接引用营销效果数字。 |
| https://devblogs.microsoft.com/microsoft365dev/microsoft-365-copilot-apis-whats-new-and-whats-next/ | 2025-11-20 | Retrieval API GA、权限裁剪与表格抽取；当时 Chat/Search 公共预览。 |
| https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview | 更新 2026-09-17 | 2026-04-01 REST GA 子集与 2026-08-01-preview 功能边界；门户预览、延迟及费用。 |
| https://dify.ai/blog | 列表含 2026-01-07、2026-02-05 | 从官方列表点击真实文章链接。 |
| https://dify.ai/blog/dify-1.12.0-summary-index-from-fragmented-retrieval-to-full-context | 2026-02-05 | 摘要字段、向量化与关联片段召回、社区版自动摘要和人工复核建议。 |
| https://dify.ai/blog/multimodal-retrieval-is-now-available-in-the-knowledge-base | 2026-01-07 | v1.11.0、多模态 embedding、图片类型与大小约束、视觉 LLM 前提。 |
| https://research.google/blog/unlocking-dependable-responses-with-gemini-enterprise-agent-platforms-agentic-rag/ | 2026-06-05 | 规划/改写/补检机制、演示说明；FramesQA 样本规模、LLM judge、跨库结果。 |
| https://docs.cloud.google.com/gemini-enterprise-agent-platform/build/rag-engine/cross-corpus-retrieval | 更新 2026-10-28 UTC | 有 API 调用文档；示例为 vertexai.preview、v1beta1，不仅是视频演示。未查到独立 GA 确认，采用保守状态标注。 |
| https://research.google/blog/bypassing-inference-bottlenecks-accelerating-complex-ai-search-with-retrieve-for-train/ | 2026-09-15 | ICML 2026 研究、离线 RL→扩散检索、53.9M 参数、12—20 倍实验加速及实验领域。 |
| https://github.com/infiniflow/ragflow/issues/12241 | 创建 2025-12-26 | 官方路线图、知识编译与 Backlogs；渲染结果不能完整还原勾选状态，因此未依靠勾选框断定交付。 |

网页回执异常及恢复：最初手工输入的 Dify Summary Index URL 为 `dify-1-12-0-...`，返回 Internal Error；之后从官方 blog 点击链接 65，打开实际 `dify-1.12.0-...` 成功。Azure 文档页顶部显示授权提示，但正文与更新日期实际可读，使用可读公开正文，没有绕过登录。

## 核对与限制

- 事实、官方自报实验结果、分析建议已区分；未把官方研究数据作为本地实测。
- 同一产品 GA/预览混合，按能力拆分；RC 不当稳定正式版。
- 计划中能找到发布证据的知识编译改归已发布。其他 Backlogs 未逐项追踪，明示“不能据此断言全部未实现”。
- 未核实 Google 该接口独立 GA 声明；正文标注为有接口的预览/成熟度待核实，避免将 SDK 名称单独当成完整服务成熟度证明。
- 未安装软件、调用收费模型、开展中文集测评、验证私有部署或采集报价；本任务为公开资料调研，这些未执行项未冒充结果。
- 五家供应商全量比较与 MatrixOrigin 公司调研不属于 T013 指定交付，未开展。
- 页面为动态网页，未来状态可能变化；本报告是查询日快照式判断，未保存完整网页镜像。

最终文件检查：result.md 与 execution.md 均存在且非空；result.md 含 12 个 HTTPS 来源链接（10 个不同 URL）。检查只验证文件与引用结构，不代表内容效果实测。

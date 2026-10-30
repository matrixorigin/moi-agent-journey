# RAG 最近一年技术进展调研

调研日：2026-10-29（Asia/Shanghai）；时间窗口：2025-10-29—2026-10-29。仅采用官方产品公告、官方文档、官方仓库和研究团队原始材料。共享输入中的公司调研、五家竞品选型和采购预算是其他主题，本报告按 T013 仅研究 RAG 技术进展，不扩展为采购比较。

## 主要判断

最近一年的实质变化是：RAG 从“一次召回几个文本片段”向可规划、可反复补证、可跨库的检索流程发展；知识准备也从切块扩展到摘要、图和树等组织方式；图片开始成为可检索的证据。企业落地同时要求权限继承、来源引用、过程日志和更新治理。下文事实支持这些趋势，但不能据此推断所有产品都已具备相同能力。

成熟度需要按具体功能判断。例如 Azure AI Search 的基础抽取检索有 GA API，而 LLM 查询规划、答案合成和多轮消息仍标为预览；RAGFlow 已发布知识编译，但 1.0 Go 重写仍是候选版本。产品名称或宣传中的“Agentic”不等于整条链路正式可用于生产。[Azure 官方文档，更新 2026-09-17](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview)、[RAGFlow 官方版本记录](https://github.com/infiniflow/ragflow/releases)。

## 已发布能力与可核查进展

| 进展 | 来源日期与具体证据 | 截至调研日的状态与边界 | 对企业知识库的意义 |
|---|---|---|---|
| 权限感知的托管检索接口 | 2025-11-20：Microsoft 365 Copilot Retrieval API 宣布 GA，支持 SharePoint、OneDrive 的权限裁剪上下文，返回相关性分数并增强表格文本抽取。[官方公告](https://devblogs.microsoft.com/microsoft365dev/microsoft-365-copilot-apis-whats-new-and-whats-next/) | **正式发布（GA）**，指 Retrieval API；同文的 Chat API、Search API 是当时的公共预览，不能一起认定 GA。本文不把旧公告的预览状态当作其今日状态。 | 检索结果继承用户权限成为产品能力，企业可减少自行维护索引的工作；不意味着支持私有部署。 |
| 图片与文本联合检索 | 2026-01-07：Dify 公告从 v1.11.0 引入多模态 embedding，支持文本与图片在同一空间检索，可配合多模态重排及视觉 LLM。[官方说明](https://dify.ai/blog/multimodal-retrieval-is-now-available-in-the-knowledge-base) | **已发布产品能力**，不自行套用云服务 GA 标签。公告所列图片路径是 Markdown 图片链接，JPG/PNG/GIF、单图最高 2MB；需要相应模型。 | 手册照片、架构图、界面截图可参与召回与推理；不能由此声称任意扫描 PDF、音频和视频均受支持。 |
| 摘要索引补充片段检索 | 2026-02-05：Dify 1.12.0 Summary Index 将 summary 与 chunk 一起向量化；匹配摘要时可返回共享或相似摘要的关联片段。[发布说明](https://dify.ai/blog/dify-1.12.0-summary-index-from-fragmented-retrieval-to-full-context) | **已发布**。支持编辑、API 导入摘要；社区版可自动生成摘要。官方建议人工检查生成质量。 | 适合“总结某方案”的整体问题，可先尝试比完整图构建更轻的方案；摘要错误和上下文膨胀仍需评估。 |
| 结构化知识准备与 Agentic 检索进入开源版本 | 2026-08-19：RAGFlow v0.27.0 发布文档/数据集级知识编译，包括 Wiki、Graph、Tree、Page Index 等，并增加 Agentic RAG 思考模式。[版本说明](https://github.com/infiniflow/ragflow/releases/tag/v0.27.0) | **已发布开源版本**。旧 GraphRAG/RAPTOR UI 被 Graph/Tree 替代；旧生成内容仍可检索。 | 索引前处理成为可配置产品层；版本迁移必须核对旧知识与新配置，不宜只看新增功能名单。 |
| 多查询检索平台的成熟度分层 | 文档更新 2026-09-17：Azure AI Search 的 2026-04-01 REST API 支持部分 GA 知识来源和 minimal 抽取检索；2026-08-01-preview 承载 LLM 规划、合成、多轮等能力。[官方文档](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-overview) | **GA 与预览混合**。API 版本号不是发布日期；门户入口整体仍是预览体验。预览不受相同 SLA 保障。 | 采购和验收须写到 API 版本、来源类型、推理级别；规划增加延迟及模型调用费用，应按问题复杂度路由。 |

上述版本发布不代表本次完成了安装或效果实测。对“提升准确率”等描述，仅确认官方发布的机制，不承诺用户数据上的效果。

## 演示、研究与预览：不能当作全面成熟能力

| 对象 | 来源日期 | 实际证据与状态 | 不能据此声称的结论 |
|---|---|---|---|
| Google 跨库 Agentic RAG 与上下文充分性检查 | 2026-06-05 研究博客；配套文档更新 2026-10-28。[研究团队原文](https://research.google/blog/unlocking-dependable-responses-with-gemini-enterprise-agent-platforms-agentic-rag/)、[API 文档](https://docs.cloud.google.com/gemini-enterprise-agent-platform/build/rag-engine/cross-corpus-retrieval) | 博客演示规划、改写、跨库检索、检查缺失证据再搜索；同时有托管接口和调用文档，已超出纯概念演示。文档示例使用 preview SDK 与 v1beta1；本次未找到独立 GA 声明，因此按**有接口的预览/成熟度待核实**处理。 | 不能将视频示例视为企业验收。官方在 FramesQA（824 问题、2,676 份 PDF）跨库设置报告 90.1% 正确率，使用 LLM 评审；这不是中文企业知识库的正确率，也不是人工审计结果。 |
| Retrieve-for-Train：把查询展开的成本转移到离线训练 | 2026-09-15，Google Research，关联 ICML 2026 论文。[原始研究说明](https://research.google/blog/bypassing-inference-bottlenecks-accelerating-complex-ai-search-with-retrieve-for-train/) | **已发表研究/实验展示**。用离线 RL 生成监督，再训练 53.9M 参数扩散检索器，研究报告相对自回归方法 12—20 倍加速。实验覆盖服饰文本到图片、音乐检索等集合检索场景。 | 未核实为可直接购买或一键部署的通用企业问答服务；不能把实验加速数字迁移到全部 RAG 链路，也未独立复现。 |
| RAGFlow 1.0 Go 重写 | 2026-09-29 rc1；2026-10-16 rc2。[官方发布记录](https://github.com/infiniflow/ragflow/releases) | **已发布候选版（RC）**，与稳定正式版区分。rc1 说明从 v0.27.2 迁移不可逆，rc2 增加 Agentic 聊天模式等。 | 不能写“1.0 已正式稳定发布”。部署前应备份、验证恢复和兼容性；未做本地部署测量。 |

## 计划与路线图

RAGFlow 官方 [ROADMAP 2026，创建于 2025-12-26](https://github.com/infiniflow/ragflow/issues/12241) 仍列有 Backlogs，如数据集推荐问题、更多数据源、增量摄入改进等。**这些是路线图条目，不是交付承诺**；本次没有逐项完成“条目→合并代码→发布版本”的核查，因此不能断言它们全部未实现，更不能纳入已交付能力。

同一份路线图中的统一知识编译已能在 v0.27.0 发布说明找到对应证据，所以该项归入“已发布”，不继续记为未来计划。路线图的创建日期也不等于其中每项功能的最新更新时间。该例说明：调研必须回到版本证据，不能照搬计划清单。

## 对中文企业知识库的行动建议（分析判断）

1. **先建立可测基线。** 用包含精确事实、跨文档关系、整篇总结、图表、无答案和无权限问题的中文问题集，分别记录检索召回、引用支持程度、答案正确性、P95 延迟和单问成本。不要把大模型评分直接等同人工确认。
2. **按问题分配复杂度。** 简单政策查询使用普通检索；跨库、多跳问题才启用规划与重试，并限制轮数、超时和预算。充分性检查也可能误判，停止条件须可观察。
3. **先补数据准备与治理。** 检查 PDF/表格解析、摘要真实性、删除和更新传播、引用定位及权限裁剪。图、树和摘要索引是否值得投入，应由同一问题集证明。
4. **分阶段试点多模态。** 先选截图或图文手册，验证图片确实进入召回和答案证据；模型与索引运行位置需逐一核实，支持自部署应用不等于所有模型调用都在内网。

本次仅做公开材料调研，无产品部署、报价采集或性能复现。FastGPT、AnythingLLM、MaxKB 未进行逐产品能力核查，因此不做它们是否落后的判断；也不据本文给出 2 万元预算的供应商中选结论。

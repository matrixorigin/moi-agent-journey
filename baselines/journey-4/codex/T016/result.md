# 企业 RAG：从入门到实践的 8 篇注释阅读清单

查询日期：2026-10-29。面向建设企业知识库问答的产品、研发和实施同学，兼顾输入中“10 人、中文、私有部署、年预算 2 万元”的使用背景。本次交付为技术阅读清单，不扩展为竞品价格或 MatrixOrigin 公司调研；全部选自厂商或项目官方公开资料。阶段、价值判断与练习为本清单的编排建议。

推荐顺序：**1–2 建立概念与架构 → 3 理解数据处理 → 4 跑通原型 → 5 理解部署 → 6 优化检索 → 7 衡量效果 → 8 完成企业安全设计**。第 8 篇应在设计阶段先浏览，上线前完整复读。

## 1. Retrieval augmented generation (RAG) and indexes

- **发布方／语言：** Microsoft Learn；英文。
- **官方原文：** [RAG and indexes](https://learn.microsoft.com/en-us/azure/foundry/concepts/retrieval-augmented-generation)
- **适读阶段：** 零基础入门；适合产品经理与初次接触 RAG 的开发者。
- **内容与价值：** 用检索、增强、生成三步解释 RAG，并介绍索引、嵌入和依据数据。帮助理解“回答基于企业文档”的实现方式，以及来源引用为何需要检索链路支持。
- **读后练习：** 画出“员工问题 → 检索结果 → 提示词 → 回答与引用”的流程，标明企业资料进入模型的位置。
- **阅读边界：** 使用 Microsoft 平台术语；先掌握通用原理，再看平台实现。RAG 减少猜测不等于保证答案正确。

## 2. Design and develop a RAG solution

- **发布方／语言：** Microsoft Azure Architecture Center；英文。
- **官方原文：** [Design and develop a RAG solution](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/rag/rag-solution-design-and-evaluation-guide)
- **适读阶段：** 理解基本概念后、启动 PoC 前；产品负责人和技术负责人共读。
- **内容与价值：** 区分应用查询流程与供给知识的数据管道，串起测试语料、分块、内容增强、嵌入、索引、搜索和逐阶段评估。可用来把“做一个聊天框”拆成可分工、可验证的工程任务。
- **读后练习：** 写一页 PoC 方案：资料来源、更新方式、测试问题、检索与回答分别如何验证，以及负责角色。
- **阅读边界：** 本篇是系列入口，适合建立地图；Azure 架构不能直接证明本地部署成本与可行性。

## 3. RAG chunking phase

- **发布方／语言：** Microsoft Azure Architecture Center；英文。
- **官方原文：** [RAG chunking phase](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/rag/rag-chunking-phase)
- **适读阶段：** 首次整理知识库、决定解析与分块策略时；主要面向研发和数据实施。
- **内容与价值：** 解释过大块的成本和噪声、过小块的上下文缺失，以及文档结构如何影响切分。对中文制度、带表格 PDF 和长手册，价值在于促使团队检查实际解析文本，而非只修改模型提示词。
- **读后练习：** 取一份公开中文手册，比较按固定长度与按标题结构切分，检查表头、适用对象和例外条件是否保留，并用同一组问题比较召回。
- **阅读边界：** 不存在通用最优块大小；需同时考虑文档类型、模型限制、质量和成本。

## 4. How to Create Knowledge in Dify (For Beginner)

- **发布方／语言：** Dify；英文；页面标注 2026-06-23。
- **官方原文：** [How to Create Knowledge in Dify](https://dify.ai/blog/create-knowledge-in-dify-for-beginners)
- **适读阶段：** 首次动手；适合产品经理、实施人员与低代码开发者。
- **内容与价值：** 按上传、分块、索引、检索设置、处理和连接知识检索节点展开，能把抽象 RAG 流程对应到真实产品操作。也解释向量、全文、混合检索与可选重排的关系。
- **读后练习：** 用公开中文 FAQ 建原型，检查切分预览，再测试同义问法、精确编号和文档外问题；保留检索片段与最终回答。
- **阅读边界：** 这是入门教程，默认设置仅用于起步；其外部模型示例不代表全链路私有部署，连接知识库也不等于完成端到端验收。

## 5. RAGFlow Quickstart

- **发布方／语言：** RAGFlow / InfiniFlow；英文；本次读取为 DEV 文档。
- **官方原文：** [RAGFlow Quickstart](https://ragflow.io/docs)
- **适读阶段：** 已有原型，开始评估本地部署与复杂文档处理；适合研发和运维。
- **内容与价值：** 从 Docker Compose 启动、健康检查，到模型配置、数据集、解析结果干预、检索测试与聊天配置，呈现完整操作路径。文档也明确本地模型需要另行配置，便于识别“应用部署在内网”和“模型也在内网”的差别。
- **读后练习：** 在隔离环境规划应用、存储、聊天模型和嵌入模型的部署位置；用公开 PDF 核对解析片段、检索命中及答案引用。
- **阅读边界：** 文档会随版本变化，实操应固定发行版并使用对应文档。快速启动不等于生产运维方案；资源配置也不能直接换算成 10 人团队总成本。

## 6. Introducing Contextual Retrieval

- **发布方／语言：** Anthropic；英文；2024-09-19。
- **官方原文：** [Introducing Contextual Retrieval](https://www.anthropic.com/research/contextual-retrieval)
- **适读阶段：** 基础 RAG 已跑通，但出现术语、编号或跨段上下文漏检时；适合研发。
- **内容与价值：** 用“切分后失去上下文”的问题引出片段上下文补充、Contextual Embeddings、Contextual BM25 和重排。适合学习如何对检索失败形成可验证的改进假设。
- **读后练习：** 固定同一中文测试集，对比纯向量、混合检索、增加上下文与重排的组合，记录召回、延迟与模型调用成本。
- **阅读边界：** 原文效果属于其数据、模型与实验配置；不能直接推断对中文企业资料的收益。上下文生成本身也增加处理成本。

## 7. List of available metrics

- **发布方／语言：** Ragas 项目官方文档；英文。
- **官方原文：** [Ragas — List of available metrics](https://docs.ragas.io/en/latest/concepts/metrics/available_metrics/)
- **适读阶段：** 已能保存问题、检索上下文和回答，准备建立回归评估时；研发与 QA 共读。
- **内容与价值：** 汇集 Context Precision、Context Recall、Response Relevancy、Faithfulness 等指标，帮助把检索质量和生成质量拆开观察，并提供各指标详细说明的入口。
- **读后练习：** 建立包含正常问法、无答案、编号、表格和多文档问题的中文测试集；按所选指标文档准备参考答案等字段，人工复核失败样本。另记录时延、成本与引用正确性。
- **阅读边界：** 该篇是指标导航，不是完整运行教程。部分指标需要 LLM 调用；自动分数需与人工审核校准，不能作为唯一上线门槛。

## 8. Retrieval-Augmented Generation (RAG) Security Cheat Sheet

- **发布方／语言：** OWASP Cheat Sheet Series；英文。
- **官方原文：** [RAG Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/RAG_Security_Cheat_Sheet.html)
- **适读阶段：** 架构设计先读；接入企业内部资料和上线前必读；产品、研发、安全与运维共读。
- **内容与价值：** 贯穿文档投毒、嵌入、访问控制继承、租户隔离、来源追踪、缓存、删除与失败关闭设计。把“资料进了知识库以后谁能问到、撤权后是否仍可访问”转为工程控制与验收问题。
- **读后练习：** 设计两个不同权限的合成用户，验证查询隔离、撤权、源文档删除后的索引与缓存清理，以及含恶意指令的文档不会越权触发操作。
- **阅读边界：** 这是实施指导，不能作为任何产品已经满足这些控制的证明；私有部署同样需要权限与数据生命周期验证。

## 建议形成的实践产物

按上述顺序，最终形成一页架构、一套公开中文样本与测试问题、一次分块／检索对照记录、逐项评估结果和权限／删除测试记录。对于年预算 2 万元的小团队，另行核算服务器、模型、解析、存储、备份和运维费用；本清单没有进行报价或预算适配验证。以上练习均为读后建议，本次未部署软件或运行 RAG 实验。

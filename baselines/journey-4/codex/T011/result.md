# 企业知识库问答软件调研

查询日期：2026-10-29（北京时间）。服务对象：矩阵起源 MatrixOrigin 的选型研究；不代表 MatrixOrigin 已采购或使用任何下列产品。

范围：Dify、FastGPT、RAGFlow、AnythingLLM、MaxKB；场景为 10 人团队、中文资料问答、私有部署、全年预算人民币 20,000 元。仅采用厂商官网、官方文档和官方代码仓库。以下能力为官方声明，未做部署或效果实测；“未公开”指本次查阅的官方页面未提供可确认报价，并非没有收费。

## 1. 客户、能力与差异

“目标客户”与“已披露客户”分开列示；客户案例是厂商披露，未独立审计效果指标。

| 产品 | 目标客户及官方披露案例 | 知识库与应用能力 | 主要差异及版本边界 |
|---|---|---|---|
| Dify | 需要搭建多类 AI 应用的开发者、业务团队及企业；官方企业页列有 A.P. Møller-Maersk、Kakaku.com、Lion Corporation。[D3] | RAG 流水线、可视化 Workflow、Agent、模型管理、可观测性。[D1] | 更接近通用 AI 应用开发平台。社区版单工作空间；多工作空间、SSO、企业治理及专属支持属于企业版，需单独核价。[D2] |
| FastGPT | 知识问答、客服和业务流程团队；官方案例包括延锋国际的 SAP 运维问答、朝阳永续的研报检索。[F4] | 文档/URL 导入、QA 拆分、分块编辑、混合检索和重排、引用反馈、工作流、MCP。[F1] | 中文业务场景资料较多。社区版有知识库、工作流、分享及 API；团队空间与权限、SSO、集合级权限、网站同步、第三方发布属于商业版。[F2] |
| RAGFlow | 复杂文档和检索工程团队；官网面向金融、法律、制造和教育，未在本次官方资料中核实具名客户。[R2] | 深度文档理解，含 PDF、影印件、表格；切片可视化、引用溯源、多路召回和重排、Agentic Retrieval。[R1] | 更强调解析及检索过程控制。官方建议起步 4 核/16GB/50GB，额外模型另算；资源与运维负担需验证。[R1] |
| AnythingLLM | 个人本地助手及需要共享工作区的小团队；本次未核实官方具名企业客户案例。[A1][A2] | 文档问答、Agent、工作区隔离、本地及云模型、可选向量库；Docker 支持多用户与管理员控制。[A1][A3] | Desktop 适合个人；10 人共用应看 Docker/server 版。MIT 许可便于组织自托管；企业 SSO 等以企业方案为准。[A1][A2] |
| MaxKB | 小团队至企业的知识问答与智能体应用；官网披露成都巴莫科技工艺知识库案例，包含一体机和实施方案，不能当作社区版开箱能力。[M3] | 文档/Web 知识库、知识入库工作流、Agent、工具/MCP/Skills、定时与事件触发、国内外及本地模型。[M1][M2] | 社区版不限用户/知识库/智能体数量；完整 RBAC、知识检索授权、操作日志、SSO 和办公平台集成等在专业/企业版。[M1] |

## 2. 中文、私有部署及 10 人适配

| 产品 | 中文证据与待验证内容 | 私有部署 | 对 10 人团队需确认的边界 |
|---|---|---|---|
| Dify | 有官方简体中文资料、中文定价页及多模型接入；本次未实测中文检索准确率。[D1][D2] | 官方 Docker Compose；文档最低 2 核/4GiB，最低配置不是生产容量保证。[D4] | 统一工作空间内部使用可考察社区版；不能把云端成员上限套给自托管版，多工作空间需求需核对许可与企业报价。 |
| FastGPT | 官方中文 README、中文案例和文档；中文模型组合需按自有资料测。[F1][F4] | 社区 Docker，自有服务器商业版亦支持。[F1][F2] | 10 位问答使用者与 10 位知识库维护者不是同一需求；社区缺团队空间及权限，不能直接认定满足多人治理。 |
| RAGFlow | 官方中文 README，支持中文资料使用路径；中文 OCR、表格与问答效果需测。[R1] | Docker 或源码；开源仓库标注 Apache-2.0。[R1] | 可作为自托管候选，但本次未逐项验证角色、文档级隔离与并发；云端 Pro 20 成员不代表自托管限制。 |
| AnythingLLM | 官方仓库提供简体中文 README，支持 DeepSeek、Ollama 等模型；本次未完成当前版本中文界面证据核验，不宣称中文 UI 已验收。[A1] | 免费 Docker；本地模型可通过 Ollama 等接入。[A1][A3] | Docker 明确多用户；需验证中文 Embedding 配置、10 账户工作区隔离与共享。Desktop 不能替代团队服务器。 |
| MaxKB | 官方中文文档，列有 Qwen、DeepSeek 等模型；中文表格、扫描件效果需测。[M2][M4] | 官方 Docker 与离线安装路径；社区单机/冷备，企业版支持集群。[M1][M2] | 用户数量不限可覆盖人数，但社区版没有完整 RBAC/对话用户授权，机密资料按人隔离时不能仅凭人数适配判定满足。 |

中文支持不等于中文问答质量保证。所有产品的 LLM、Embedding、重排、OCR 与分块策略都会影响效果。“应用部署在内网”也不等于“数据不出网”：选用外部模型 API、解析服务或连接器时仍可能外发内容；全离线需要逐个组件核验。

## 3. 公开价格（查询日口径）

金额保留原币种，不做未经核实的汇率换算。软件授权费与服务器、存储、模型、实施及运维费分别预算；免费开源软件不等于零总成本。

| 产品 | 自托管软件与商业价格 | 云服务参考，不能替代私有部署报价 |
|---|---|---|
| Dify | Community 免费；企业版定制报价，金额未公开。社区版遵守附加条件的许可。[D2][D5] | Professional $590/工作空间/年，3 名成员；Team $1,590/工作空间/年，50 名成员；显示价格不含适用税费。10 位协作者不能使用 Professional 的 3 人配额。[D2] |
| FastGPT | 社区版可自托管；自有服务器商业版报价未公开，需询价。全托管 8C32G 为 ¥120,000/年起、多节点 32C128G 为 ¥264,000/年起，均超过预算。[F1][F2] | Free ¥0/月（1 成员）；Basic ¥99/月（5 成员）；Advanced ¥599/月（50 成员）；页面标注年付按 10 个月计，Advanced 据此为 ¥5,990/年，仅为云套餐计算，非私有部署价格。[F3] |
| RAGFlow | Apache-2.0 开源自托管；带专属支持/SLA 的 Enterprise 私有部署报价未公开。[R1][R2] | Free $0（1 成员）；Starter 5 成员，页面同时呈现 $29 和 $59/月；Pro 20 成员，同时呈现 $129 和 $259/月。本次文本提取未保留促销或周期样式，不能确认两个价格的适用条件，不选其中一个作采购报价。[R2] |
| AnythingLLM | Docker 免费；Enterprise 私有部署、SSO、定制支持价格未公开。[A2] | Basic $50/月，自带模型 API Key；Pro $99/月；按 12 个月机械年化为 $600/$1,188，不表示年付折扣。公开页未提供明确成员配额，10 人需确认。[A2] |
| MaxKB | 社区版免费；专业版 ¥48,000/套永久授权，含一年维保，第二年起维保 ¥9,600/年；每套对应一个最终客户的一个部署实例；企业版价格未公开。[M1] | 本次未核实统一官方 SaaS 套餐报价，不填第三方代理价格。 |

授权补充：Dify 许可允许商业使用，但多租户环境及前端 Logo/版权修改有附加条件；FastGPT 同类多租户 SaaS 及控制台 Logo/版权有附加条件，不能笼统写成纯 Apache-2.0。AnythingLLM 为 MIT，RAGFlow 为 Apache-2.0，MaxKB 仓库为 GPLv3。[D5][F5][A1][R1][M2] 内部使用与 MatrixOrigin 将来对外提供服务是两种范围，后者需另核许可。

## 4. 预算与采购核验口径

20,000 元是全年总预算，先设一个可调整的预算上限，而非冒充供应商报价：服务器/存储/备份 6,000 元，模型/Embedding/OCR 5,000 元，部署维护 6,000 元，预备金 3,000 元，合计 20,000 元；若购买授权，必须从这些额度重分配。已有机器也应记录是否把人员成本与折旧排除在预算外。

| 需求条件 | 对选型的实际影响 |
|---|---|
| 仅少数维护者，10 人读取同一非敏感资料库，已有运维能力 | 五款社区自托管均可进入概念验证；仍需逐项确认权限、模型与实际资源成本。现有资料不足以保证任一款总成本必定小于 2 万元。 |
| 必须原厂商业支持、SSO 或细粒度文档权限 | MaxKB 专业版首购 48,000 元已超首年预算；FastGPT、Dify、RAGFlow、AnythingLLM 商业私有部署尚无可确认的预算内报价。 |
| 资料以扫描 PDF、复杂表格为主 | 对 RAGFlow 深度解析做重点验证，并用同一组材料对其他产品测试，避免用功能清单代替解析效果。 |
| 全离线并新购 GPU | 当前公开资料不能证明 2 万元足够；先锁定中文本地模型、并发目标和硬件清单，再核全成本。 |

采购前以同一份不含真实客户数据的中文语料验证：DOCX、原生 PDF、扫描 PDF、表格各一组；至少 50 个有标准答案的问题、10 个资料外问题。记录回答正确性、引用是否支持结论、拒答、10 人并发延迟、越权检索、文档更新/删除后可见性、备份恢复和费用。候选是否进入采购取决于这些实测及正式报价；本报告不把未执行的验证写成已通过。

## 5. 官方来源目录

以下全部查询于 2026-10-29。

- D1：[Dify 官方 README](https://raw.githubusercontent.com/langgenius/dify/main/README.md)
- D2：[Dify 官方定价](https://dify.ai/zh/pricing?plan=self-hosted)
- D3：[Dify Enterprise 客户及企业能力](https://dify.ai/dify-enterprise)
- D4：[Dify Docker Compose 部署](https://docs.dify.ai/en/self-host/deploy/quick-start/docker-compose)
- D5：[Dify LICENSE](https://raw.githubusercontent.com/langgenius/dify/main/LICENSE)
- F1：[FastGPT 官方中文 README](https://raw.githubusercontent.com/labring/FastGPT/main/README.md)
- F2：[FastGPT 商业版功能及部署报价](https://doc.fastgpt.io/en/guide/version/commercial)
- F3：[FastGPT 官方定价](https://fastgpt.io/price)
- F4：[FastGPT 官方客户案例](https://fastgpt.cn/customers)
- F5：[FastGPT LICENSE](https://raw.githubusercontent.com/labring/FastGPT/main/LICENSE)
- R1：[RAGFlow 官方中文 README](https://raw.githubusercontent.com/infiniflow/ragflow/main/README_zh.md)
- R2：[RAGFlow 官网及价格](https://ragflow.io/)
- A1：[AnythingLLM 官方 README](https://raw.githubusercontent.com/Mintplex-Labs/anything-llm/master/README.md)
- A2：[AnythingLLM 官方 Cloud/价格](https://anythingllm.com/cloud)
- A3：[AnythingLLM Docker 安装与多用户说明](https://docs.anythingllm.com/installation-docker/overview)
- M1：[MaxKB 官方版本及价格对照](https://maxkb.cn/price/)
- M2：[MaxKB 官方 README](https://raw.githubusercontent.com/1Panel-dev/MaxKB/main/README.md)
- M3：[MaxKB 成都巴莫科技案例](https://www.maxkb.cn/blog/maxkb-bamo-ai-process-knowledge-platform)
- M4：[MaxKB 官方中文文档](https://docs.fit2cloud.com/maxkb/)

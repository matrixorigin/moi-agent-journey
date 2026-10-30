# T016 执行记录

## 范围与输入

- 工作目录：本 case T016；只写入 results/T016。
- 实际读取：AGENTS.md、TASK.md、samples/T016/task.md、samples/shared/research.md；另读授权引用的公共工具规则 <local-RTK-instructions>。
- 当前任务仅要求 8 篇企业 RAG 阅读资料，未扩展为共享输入中的公司调研或五款竞品采购比较。
- 未读父目录、其他 case、历史或记忆；未委派、发信、下单或公开发布。

## 实际 shell 命令及回执

1. `rtk proxy cat AGENTS.md TASK.md`：exit_code=0，读取 case 约束与任务。
2. `rtk proxy cat <local-RTK-instructions> samples/T016/task.md samples/shared/research.md`：exit_code=0，确认 RAG 阅读任务及企业知识库背景。
3. `rtk proxy python3 - <<'PY' ... PY`：实际通过 pathlib 创建 results/T016 并写入 result.md、execution.md；正文含 8 篇官方来源及逐篇注释。完整写入脚本在本轮工具调用中，未将示例代码冒充研究实操。
4. 文件落盘后的核对命令及结果见末尾核对记录。

## 实际互联网查询

使用 web.run 实时检索及打开正文，查询日期 2026-10-29。

第一组 search_query：
- site.microsoft.com learn azure architecture retrieval augmented generation overview
- site.anthropic.com contextual retrieval
- site.docs.dify.ai knowledge retrieval testing
- site.ragflow.io docs deepdoc

第二组 search_query：
- site.docs.dify.ai 知识库 创建 知识库 检索 测试
- site.docs.ragas.io evaluation metrics context precision faithfulness
- site.learn.microsoft.com azure search rag security document level access
- site.learn.microsoft.com rag chunking phase

搜索结果包含第三方镜像、论坛和论文；最终未采用这些第三方资料。Dify 文档镜像 dify-docs.docs7.io 未作为官方来源，而采用 dify.ai 官方教程。

## 最终来源与正文核对回执

以下 8 页均通过 web.run open 成功返回 text/html 正文；链接见 result.md，构成最终清单。只做阅读检索，无计算或基准测试。

| # | 来源 | 实际核对的内容／工具回执 |
|---|---|---|
| 1 | Microsoft RAG and indexes | 原 ai-foundry 链接重定向到 /azure/foundry/concepts/retrieval-augmented-generation；返回 160 行，核对三步流程与索引概念。 |
| 2 | Microsoft Design and develop a RAG solution | 返回 157 行，核对系列范围及应用／数据管道。 |
| 3 | Microsoft RAG chunking phase | 返回 372 行，核对块大小、上下文、结构和成本取舍。 |
| 4 | Dify How to Create Knowledge in Dify | 返回 227 行；另定位第 88 行附近，核对分块、索引、检索选项和接入节点；页内日期 2026-06-23。 |
| 5 | RAGFlow Quickstart | 返回 223 行；另定位第 115 行附近，核对 Docker、健康检查、模型、解析、检索和聊天；页面选择器为 DEV，示例版本 v1.0.0-rc2。 |
| 6 | Anthropic Introducing Contextual Retrieval | 返回 220 行，核对上下文补充、BM25、嵌入与重排；页内日期 2024-09-19。 |
| 7 | Ragas List of available metrics | 返回 288 行；另定位第 230 行附近，核对 RAG 指标与 LLM 评分调用说明。 |
| 8 | OWASP RAG Security Cheat Sheet | 返回 584 行；另定位第 280 行附近，核对投毒、权限元数据、租户隔离、监控与失败关闭等控制。 |

Microsoft 页面模板含“Access to this page requires authorization”提示，但本次工具实际同时返回了以上公开正文；本结果仅使用已返回内容，不声称验证了受限区。其他检索候选（Azure AI Search 权限控制等）未计入最终八篇。

## 口径与限制

- 8 篇按页面计数，同一系列的总览与分块专题是两个不同页面，价值和适读阶段不同。
- 每篇内容摘要基于官方正文；适读阶段、练习及阅读路径为本次整理建议。
- 没有执行 RAGFlow/Dify 部署、模型调用或评估实验；练习均明确标为建议。
- 未查询采购价格，未验证中文模型效果、生产安全能力、全私有部署或年预算适配。
- 没有将厂商效果百分比写成目标项目收益；未发生阻碍本次阅读清单交付的工具故障。

## 落盘核对记录

实际执行 `rtk proxy python3` 读取两份文件，断言正文编号条目=8、官方链接=8、链接去重=8，两文件非空且 UTF-8 可读；命令 exit_code=0。写入命令回执为 Written result.md and execution.md / Reading entries: 8。

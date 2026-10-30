# T011 执行记录

执行日期：2026-10-29；执行范围仅本 case，输出仅 results/T011。未读取其他 case、历史、记忆或他产品答案，未委派，未自评分。未进行部署、下单、联系销售或访问客户私有资料。

## 读取输入

在本 case 工作目录依次实际执行，退出码均为 0：

- `rtk read AGENTS.md`：确认 case 边界与 RTK 要求。
- `rtk read TASK.md`：确认 T011 调研任务与输出路径。
- `rtk read <local-RTK-instructions>`：读取 AGENTS 引用的公共执行规范。
- `rtk read samples/T011/task.md`：确认交付说明。
- `rtk read samples/shared/research.md`：读出五个指定竞品；MatrixOrigin；中文、私有部署、10 人、20,000 元/年要求。

## 实际联网查询

使用 `web.run` 搜索和打开官方页面。搜索会返回第三方结果，但正文只引用官方来源，没有采用 fastgpt.wang、fastgptcn.com、anythingllmcn.com 等相似域名报价。

实际搜索词：

1. `site.dify.ai pricing self hosted Chinese knowledge base Dify`
2. `site.fastgpt.io 价格 私有部署 FastGPT`
3. `site.ragflow.io pricing self hosted RAGFlow`
4. `site.anythingllm.com pricing self hosted Chinese`
5. `site.maxkb.cn 价格 社区版 专业版 企业版`
6. `site.fastgpt.io docs commercial license self hosted FastGPT`
7. `site.dify.ai customer stories company enterprise`
8. `site.docs.anythingllm.com Chinese language supported`
9. `site.maxkb.cn 客户 案例`
10. `site.ragflow.io customer case study`
11. `site.anythingllm.com customers case study`
12. `site.fastgpt.cn 客户 案例`

实际打开并读到内容的官方页面列于 result.md D1—D5、F1—F5、R1—R2、A1—A3、M1—M4；另外打开官方 GitHub 仓库 https://github.com/infiniflow/ragflow 和 https://github.com/labring/FastGPT，用于确认证据归属。AnythingLLM `/pricing` 自动跳转 `/cloud`；MaxKB `/docs/v2/index.html` 跳转 `https://docs.fit2cloud.com/maxkb/`；Dify 原 Docker 文档路径跳转当前 `/en/self-host/deploy/quick-start/docker-compose`。

部分页面首次打开仅返回截断片段；后续按行打开补充 FastGPT 商业价、RAGFlow 价格、Dify 配置和 README。FastGPT 商业文档最终读到自有服务器联系报价、全托管 120,000 元/年起及多节点 264,000 元/年起。RAGFlow 页面两组月价样式未完整保留，因此保留两个原值及解释限制。

AnythingLLM 中文 UI 路径核验失败：官方仓库的 `frontend/src/locales/zh-CN/common.js`、`common.ts`、`frontend/src/locales/zh-CN.js` 均返回 404。没有把不存在的文件当成中文界面支持证据，改用官方简体中文 README 与模型适配说明，并在正文明确 UI 未验。

## 实际 shell 操作及回执

全部 shell 命令以 `rtk` 开头，工作目录为当前 case。

1. `rtk proxy python3 - <<'PY' ... PY`：使用 pathlib 创建 results/T011，urllib.request 下载 11 个官方源，保存成功响应原文和 `source_receipts.json`。实际每次请求的 URL、HTTP 状态、字节数、UTC 时间及失败原因见该 JSON；脚本退出码 0（内部逐项记录 HTTP 错误）。
2. `rtk proxy python3 - <<'PY' ... PY`：检查商业文档是否已下载并尝试 HTML 文本提取，输出 `download still pending`。当时前一步后台脚本仍运行，未生成所谓提取结果文件；后续知该 URL 返回 403，通过 web.run 阅读。
3. 使用进程会话读取下载脚本完成输出：7 个下载成功、3 个 HTTP 403、1 个 HTTP 404。Dify 定价、FastGPT 商业文档和 MaxKB 定价虽然直接 urllib 下载为 403，但 web.run 成功打开并返回官方正文，故正文价格来源为 web.run，而非本地快照。
4. `rtk proxy python3 - <<'PY' ... PY`：以 UTF-8 写入 result.md，回执 `written results/T011/result.md 5799 characters`。
5. 最后执行同类 Python 写入本 execution.md、检查结果非空、核对指定产品与来源数量、执行机械年化计算并生成文件清单和 SHA-256。

下载快照仅供来源追溯，非运行产品产生的业务数据。未运行 README 中的安装指令。

## 数据核对与口径

- 五个指定名称均出现在正文，均覆盖目标客户/案例、能力、差异、价格、中文、私有部署及 10 人边界。
- MaxKB 专业版首购 48,000 元 > 首年 20,000 元预算；不是只拿次年 9,600 元维保判断首年预算。
- FastGPT Advanced 云价：599 × 10 = 5,990 元/年；仅按公开“年付按 10 个月”计算，不等同私有部署报价。
- AnythingLLM 云服务机械年化：50 × 12 = 600 美元；99 × 12 = 1,188 美元；无推断折扣。
- 自拟预算：6,000 + 5,000 + 6,000 + 3,000 = 20,000 元，仅是支出上限分配，无供应商报价含义。
- 未进行汇率换算；不把工作空间成员与聊天使用者混为一谈；不把社区许可、云付费套餐和企业私有部署合并。

## 限制与未完成部分

未获得各企业版本正式报价，未核税费、合同与长期维保条款；具名客户只记录本次能核实的官方披露，RAGFlow/AnythingLLM 未核到具名客户，不编造。没有实测中文界面完整度、解析质量、问答效果、权限隔离、10 人并发、资源成本或全离线运行，正文均未声称验收通过。官方网页价格可变化，本报告仅对应查询日。

任务要求的调研正文和执行记录已实际生成；不额外声称已安装任一产品。

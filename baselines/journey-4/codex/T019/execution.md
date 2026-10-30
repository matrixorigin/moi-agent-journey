# T019 执行记录

日期：2026-10-29。工作目录：本 T019 case 根目录；只读取本 case 材料与公开工具规则/官方软件资料。没有读取历史、记忆、其他 case 或评分资料；没有委派、发信、下单或公开发布。

## 本地读取与回执

实际通过 exec_command 执行以下 shell（均以 rtk 开头），exit_code 均为 0：

- `rtk read AGENTS.md`：要求只执行当前 TASK、仅本 case 写入、不委派。
- `rtk read TASK.md`：任务为 10 人、中文、私有部署、年预算 2 万元，选 3 款并记录来源与执行。
- `rtk read <local-RTK-instructions>`：规则为 shell 必须 rtk 前缀。
- `rtk read samples/T019/task.md`：确认输入和结果目录。
- `rtk read samples/shared/research.md`：知识库问答软件，范围 Dify/FastGPT/RAGFlow/AnythingLLM/MaxKB，只比官方公开资料。
- `rtk proxy python3 - <<'PY' … PY`：实际创建当前目录下 results/T019，UTF-8 写入 result.md 与 execution.md；脚本使用 pathlib，仅写指定结果目录。

## 实际互联网查询

使用 web.run 的 search_query/open/find；未使用搜索摘要中的第三方价格作为结论。search_query 实际检索串如下：

1. `site.docs.dify.ai self-host docker Chinese license`
2. `site.fastgpt.io 私有部署 商业版 价格`
3. `site.maxkb.cn 社区版 专业版 价格`
4. `site.docs.anythingllm.com Chinese language multi-user Docker`
5. `site.docs.dify.ai community edition unlimited members self hosted`
6. `site.docs.dify.ai zh 中文 语言`
7. `site.docs.dify.ai self hosted members invite community workspace`
8. `site.docs.dify.ai "Community" "members"`

open 实际访问五个项目官方 GitHub 主仓库、Dify/AnythingLLM/RAGFlow 的原始 README、FastGPT/MaxKB 价格页，及下面正文采用的官方资料。find 定位 RAGFlow README 的 CPU、AnythingLLM README 的 Multi、Dify README 的 Community。关键回执：

| 官方地址 | 实际返回内容/核对值 |
|---|---|
| https://maxkb.cn/price | 社区免费/用户无限；专业版 48,000 元永久授权；第二年维保 9,600 元；逐用户检索权限、RBAC 等版本差异 |
| https://raw.githubusercontent.com/1Panel-dev/MaxKB/v2/README_CN.md | RAG、本地模型、Linux/Windows Docker、内网离线安装；GPLv3 |
| https://raw.githubusercontent.com/1Panel-dev/MaxKB/v2/README.md | Docker、自托管、模型中立；与中文资料相互核对 |
| https://docs.anythingllm.com/features/security-and-access | Docker 才有多用户；Admin/Manager/Default 角色；Default 限指定工作区 |
| https://docs.anythingllm.com/installation-docker/local-docker | 本地 Docker 安装资料，网页正常返回 |
| https://docs.anythingllm.com/installation-docker/overview | 检索返回 Docker 单/多用户和本地模型/RAG 支持 |
| https://github.com/Mintplex-Labs/anything-llm | 官方仓库 MIT 标识 |
| https://raw.githubusercontent.com/Mintplex-Labs/anything-llm/master/README.md | 多用户、文档流水线、本地模型，Docker-only 权限 |
| https://raw.githubusercontent.com/Mintplex-Labs/anything-llm/master/frontend/src/locales/zh/common.js | 中文界面及团队/账号/工作区文字 |
| https://www.dify.ai/pricing | 免费社区版、单工作区、商业版询价；云套餐不能冒充社区版额度 |
| https://docs.dify.ai/en/self-host/use-dify/workspace/team-members-management | 4 类内建角色、成员邀请、CONSOLE_WEB_URL/MAIL_TYPE；没有明确写社区版 10 人席位上限 |
| https://raw.githubusercontent.com/langgenius/dify/main/web/i18n/languages.ts | zh-Hans 与 zh-Hant 均 supported: true |
| https://raw.githubusercontent.com/langgenius/dify/main/LICENSE | 修改 Apache 2.0；多租户及移除品牌限制 |
| https://docs.dify.ai/en/self-host/quick-start/docker-compose | 重定向至 /en/self-host/deploy/quick-start/docker-compose；最低 2 核/4 GiB；多组件部署 |
| https://doc.fastgpt.io/en/guide/version/commercial | 社区版无团队空间/权限；商业版自托管询价；全托管 120,000 元/年起；技术服务 2,000–3,000 元/人天 |
| https://fastgpt.io/price | 默认返回云套餐及 Self-Hosted 切换入口，没有把云价用于私有报价 |
| https://raw.githubusercontent.com/infiniflow/ragflow/main/README_zh.md | 本地 Docker；建议 4 核/16 GB/50 GB；附加本地模型另需资源 |
| https://github.com/infiniflow/ragflow | Apache-2.0 标识 |

额外 open 的 Dify 官网中文页及官方 README 用于确认产品/部署来源；MaxKB 文档入口 https://maxkb.cn/docs/v2/ 正常跳转至 https://docs.fit2cloud.com/maxkb/。

## 错误、舍弃与限制

- Dify 原始语言文件旧路径 `/web/i18n/zh-Hans/common.ts` 返回 404，随后成功读取官方 `/web/i18n/languages.ts`，没有把错误路径当支持证据。
- `https://docs.dify.ai/en/self-host/deploy/faq` 无法访问；最终直接读取官方成员文档完成邀请能力核对。未由 FAQ 失败推断功能不存在。
- 搜索命中 fastgpt.wang、fastgpt.cn 技术中心转述、Yuanspace、第三方中文 AnythingLLM 文档、Dify 镜像文档、alibaba/dify fork、论坛/第三方教程等；未以这些作为正文关键证据。镜像成员文档只用于定位，最终以 docs.dify.ai 同路径成功返回内容引用。
- 价格是查询日页面显示值；动态分栏价格未完整渲染时不臆造自托管费。没有联系销售取得书面报价，没有软件安装、中文效果测试、10 人实际登录或并发压测。
- 不知道文档规模、问答量、硬件/人力现状及是否完全离线；因此不能核定全年 TCO。三款是条件候选，不声称全部硬指标已经实测通过。
- 预算分配 6,000 + 6,000 + 6,000 + 2,000 = 20,000 元，仅为内部封顶示例；月均上限 20,000 / 12 ≈ 1,667 元。没有把它写作厂商报价。

结果文件写入后以 rtk proxy python3 读取检查文件存在、UTF-8 可读与大小；不进行自评分。

实际最终核验回执：result.md 与 execution.md 均存在且可按 UTF-8 读取；命令 exit_code=0。核验只读取指定两文件并追加本回执。

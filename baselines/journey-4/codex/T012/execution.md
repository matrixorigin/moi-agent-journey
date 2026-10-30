# T012 执行记录

执行日期：2026-10-29；工作目录为本 case 根目录。未使用记忆、其他 case、其他产品答案或评分材料，未委派，未接触真实客户私有资料，未发送消息或公开发布。

## 输入读取与 shell 回执

1. `rtk proxy cat AGENTS.md TASK.md`：exit_code=0，读取范围规则及 T012 目标。
2. `rtk proxy cat <local-RTK-instructions> samples/T012/task.md samples/shared/research.md`：exit_code=0。确认 shell 必须以 rtk 开头；共享材料含多个独立调研主题，本次只做公司调研。
3. `rtk proxy python3 - <<'PY' ... PY`：实际创建 results/T012/result.md 与 execution.md，正文和本记录以 UTF-8 保存。完整生成内容见实际文件；没有将示例代码当作产物。
4. 随后以 rtk proxy python3 核对文件存在、非空、UTF-8 可读和场景数量，回执附于文末。

## 实际网络查询

使用 web.run 的 search_query，真实查询词：
- `矩阵起源 MatrixOrigin 公司 创始人 2026 MatrixOne MatrixFlow`
- `site.matrixorigin.cn 关于我们 矩阵起源`

搜索结果含第三方页面，但正文事实只取官方页面。随后实际 open 官方 about、首页、融资公告、blog 索引；由索引 click 最新项目、开发者模式和公测文章；由 about click Agentic RAG，再 open 读取其正文和公测正文。上述读取均返回网页内容，没有登录产品系统、执行网页内安装脚本或访问客户系统。

## 使用来源及核对

全部于查询日实际读取：
- https://matrixorigin.cn/about ：成立年份、团队规模及职务、融资总额、办公室；无页面发布日期，视为查询日官网快照。
- https://matrixorigin.cn/ ：MatrixOne/MOI 产品定位；官网业绩宣传数字未用于推导目标公司的收入或采购预算。
- https://matrixorigin.cn/solution/agentic-rag ：知识库能力和案例入口；未实测性能。
- https://matrixorigin.cn/blog/matrixorigin-series-a-financing ：文章日期 2026-08-06；A 轮与资金用途、客户行业及私有部署描述。
- https://matrixorigin.cn/blog/shenzhen-unicom-smart-manufacturing-ai-project ：文章日期 2026-10-27；签约、开发交付状态和五个规划场景。实际点击 URL 带版本参数 `?v=20261027_0a17232`；正文采用规范 URL。
- https://matrixorigin.cn/blog/moi-developer-mode-astra ：文章日期 2026-10-26；Astra、moi-cli 与开发者模式。实际点击 URL 同带上述版本参数。
- https://matrixorigin.cn/blog/moi-5-second-round-beta ：文章日期 2026-10-23；受邀公测、新增读入能力和定价入口。实际点击 URL 同带上述版本参数。
- https://matrixorigin.cn/blog ：用于发现上述最新文章，防止将 2025 产品发布误当最近动态。

事实口径：只确认“官方披露此事”；未做工商、融资到账、客户证言或线上运行的独立审计。近期定义为截至查询日近三个月，并重点列当月事件；没有将将来的活动当成已发生事件。姓名采用中文官网“田丰”，未采用第三方“田峰”写法。

计算口径：没有营收或预算估算。试点题量与验收阈值均为研究建议，无观测样本，不是实际测试结果。正确率=正确回答数/相应题目数；引用可核对率=可回溯且支持答案的引用数/全部引用数；拒答率针对无答案题；耗时改善按同一题集人工与试点中位耗时对比。

## 限制与未执行

未取得采购意向、预算、权限设计细节、内部工具栈、完整组织图及采购决策人；未开展访谈、产品试用、部署、技术性能验证或试点。私有部署商业报价未公开取得。三种场景及优先级均为假设，不能作为已有商机或成交概率。除这些明确的后续验证外，本次所要求的公开公司调研正文及执行记录已实际写入。

## 文件验证回执

```text
results/T012/result.md: exists=True; utf8=True; bytes=8184
results/T012/execution.md: exists=True; utf8=True; bytes=3917
result.md: three numbered scenarios verified
```
验证命令退出码 0；execution.md 的字节数为追加本回执前值。

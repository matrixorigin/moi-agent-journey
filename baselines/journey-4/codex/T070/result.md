# shared 资料主题索引

覆盖 `samples/shared/` 的全部 64 份资料；以下路径均相对于本 case 根目录。每份资料仅列入一个主要主题，每行一句用途。所有业务资料为合成样例，基准日期为 2026-10-29（Asia/Shanghai）。

不同资料保留各自时间背景和版本，例如访谈时预算未批准、后续销售沟通时试点预算已批准，不在索引中擅自统一；录音内容未转写的限制在对应条目中列明。

## 产品需求、版本与决策（11 份）

| 相对文件路径 | 用途 |
|---|---|
| `samples/shared/idea.md` | 用于明确采购比价助手的 V1 范围、人工审阅流程及超过 5 万元的审批要求。 |
| `samples/shared/feature.md` | 用于定义报价比较功能的输入字段、缺失项提示、导出流程与角色权限。 |
| `samples/shared/prd.md` | 用于审查采购比价 PRD 草案中的范围、自动下单、可见权限与验收口径矛盾。 |
| `samples/shared/product.md` | 用于引用青岚报价助手的现有能力、试点证据和自动下单等能力边界。 |
| `samples/shared/decision.md` | 用于评估自动下单是否适合试点及其运费、ERP、撤单和资源限制。 |
| `samples/shared/versions.md` | 用于对照新旧版本的工作区、会话归档及税运费规则并约束旧数据迁移。 |
| `samples/shared/releases.md` | 用于编写 2026-10-28 已发布功能说明并区分内部重构和未发布的自动下单。 |
| `samples/shared/planning.md` | 用于依据后端、前端和 QA 容量及需求依赖规划 11 月迭代。 |
| `samples/shared/feedback.csv` | 用于汇总客户反馈、合并重复问题并结合影响程度安排优先级。 |
| `samples/shared/reviews.csv` | 用于分析用户评分及引用出处、上传失败、表格准确性等评价主题。 |
| `samples/shared/funnel.csv` | 用于计算访问到分享报告各步骤的转化与流失。 |

## 客户、销售与续约（12 份）

| 相对文件路径 | 用途 |
|---|---|
| `samples/shared/customer.md` | 用于准备青岚制造客户演示并确认需求、决策角色、已批试点预算和决策时间。 |
| `samples/shared/interview.md` | 用于提炼采购、财务、IT 的访谈需求与试点约束并保留当时预算未批的时间背景。 |
| `samples/shared/sales_call.md` | 用于整理客户销售沟通中的试点范围、审批角色、演示日期和部署清单承诺。 |
| `samples/shared/renewals.csv` | 用于结合续约日、合同价值、活跃趋势和未结工单识别续约风险。 |
| `samples/shared/usage.csv` | 用于对比客户月度活跃天数、运行量和错误次数。 |
| `samples/shared/customers_crm.csv` | 用于提供 CRM 客户标识、名称和邮箱作为跨系统匹配来源。 |
| `samples/shared/customers_billing.csv` | 用于提供账务账户、公司、邮箱与余额以核对 CRM 匹配及同名歧义。 |
| `samples/shared/customers_dirty.csv` | 用于识别客户数据的重复记录、空白、无效邮箱与缺失字段。 |
| `samples/shared/sales.csv` | 用于按月份、客户、渠道和产品分析销售订单并区分已付款与已取消记录。 |
| `samples/shared/daily.csv` | 用于定位日收入波动及对应产品、地区和客户。 |
| `samples/shared/wide.csv` | 用于将按月份横向排列的产品数据整理成长表或进行趋势比较。 |
| `samples/shared/cohort.csv` | 用于根据注册月与付费月分析用户付费队列及后续留存。 |

## 采购、供应商与库存（7 份）

| 相对文件路径 | 用途 |
|---|---|
| `samples/shared/quotes.csv` | 用于比较三家供应商的价格、税费、运费、交期和质保并标识缺失运费。 |
| `samples/shared/purchase_requests.csv` | 用于按物料与规格汇总采购需求并结合需要日期安排采购。 |
| `samples/shared/inventory.csv` | 用于结合现存量、日需求、交期、安全库存和在途量判断补货需求。 |
| `samples/shared/demand.csv` | 用于依据 12 个月的需求数量观察趋势及进行需求预测。 |
| `samples/shared/supplier_orders.csv` | 用于比较供应商订单的交付及时性、缺陷率和价格表现。 |
| `samples/shared/sop.md` | 用于将采购标准流程与口头流程、实际执行记录逐项核对并找出漏审批或漏核运费。 |
| `samples/shared/flowchart.png` | 用于查看采购流程及审批通过、驳回分支并识别缺少运费时的分支缺口。 |

## 财务、合同与投标（12 份）

| 相对文件路径 | 用途 |
|---|---|
| `samples/shared/budget.csv` | 用于按部门对比预算与实际支出并结合备注解释差异。 |
| `samples/shared/expenses.csv` | 用于按部门、项目和费用类型汇总支出并定位异常大额费用。 |
| `samples/shared/cost.md` | 用于估算销售项目成本与利润并列明运费、差旅、税费和维护人力等未列成本。 |
| `samples/shared/invoices.csv` | 用于按供应商、金额和到期日管理应付发票及逾期事项。 |
| `samples/shared/payments.csv` | 用于与发票核销付款并检查重复支付、少付和未付款。 |
| `samples/shared/contracts.csv` | 用于按合同结束日、付款日及金额安排履约和资金提醒。 |
| `samples/shared/contract.pdf` | 用于查阅 Q2026 采购服务合同的价款、付款、交付、质保、违约金和资料删除条款。 |
| `samples/shared/contract_old.md` | 用于作为 Q2026 旧版合同基线，与新版逐项比较商务及数据使用条款。 |
| `samples/shared/contract_new.md` | 用于审查 Q2026 新版合同中价款、账期、交付、质保、违约责任及资料使用的变化。 |
| `samples/shared/receipt_scan.png` | 用于从 QL-001 合成票据图片提取日期、商品、数量、税额和总额并标记运费未注明。 |
| `samples/shared/scan.pdf` | 用于从扫描版 QL-001 合成票据核对商品、数量、税额和总额及运费缺失情况。 |
| `samples/shared/tender.md` | 用于建立投标资格、技术、交付材料与截止日期清单并识别未给出的并发量和保证金规则。 |

## 项目交付、运维与会议（9 份）

| 相对文件路径 | 用途 |
|---|---|
| `samples/shared/projects.md` | 用于跟踪三个项目的负责人、进展、依赖、延期原因及历次会议承诺。 |
| `samples/shared/weekly.md` | 用于整理本周成果、阻塞与下周安排并去除重复工作记录。 |
| `samples/shared/incident.md` | 用于还原上传故障发布、发现、回滚和恢复时间线及影响范围并保留根因待核实状态。 |
| `samples/shared/complaint.md` | 用于起草上传故障投诉回复与处理选项并约束调查承诺和未获依据的赔偿表态。 |
| `samples/shared/tickets.csv` | 用于按客户、主题、日期和状态统计支持工单及未结问题。 |
| `samples/shared/migration.md` | 用于制定生产迁移、快照保留、去重、停机窗口和失败回退方案。 |
| `samples/shared/meeting.txt` | 用于查阅采购组试点决定、已批预算及部署清单、安全审查、演示等行动日期的文本记录。 |
| `samples/shared/meeting.wav` | 用于从会议录音核验讨论和整理纪要，已确认其为约 25.30 秒单声道 WAV，语音内容尚未转写核实。 |
| `samples/shared/report_50pages.pdf` | 用于查阅 50 章采购试点报告的耗时证据与例外，尤其第 37 章要求先修复的跨部门权限泄露。 |

## 研究、内容、品牌与语言（8 份）

| 相对文件路径 | 用途 |
|---|---|
| `samples/shared/research.md` | 用于明确企业知识库竞品、MatrixOrigin 公司与 RAG 技术调研范围及官方来源要求。 |
| `samples/shared/articles.md` | 用于评估五份内部试验材料的证据强弱并列出需要官方来源核查的文章论断。 |
| `samples/shared/jobs.md` | 用于根据目标公司招聘岗位快照形成有边界的人才需求观察，避免推断未给出的预算和战略。 |
| `samples/shared/brand.md` | 用于统一品牌语气和产品术语并改写夸大准确率、替代员工及免费额度的营销表述。 |
| `samples/shared/content.csv` | 用于比较内容主题的阅读、线索、成本与获客效率。 |
| `samples/shared/event.md` | 用于起草报价比较线上分享的活动内容与渠道安排并保留试点能力边界。 |
| `samples/shared/english.md` | 用于翻译或校对采购试点英文报告并保留标识符、引用及无对照组的证据限制。 |
| `samples/shared/bilingual.md` | 用于核对中英文在试点单量、耗时、保留期、自动下单能力和术语上的不一致。 |

## 员工指引、学习与行政安排（5 份）

| 相对文件路径 | 用途 |
|---|---|
| `samples/shared/notes.md` | 用于把新员工笔记整理为账号权限、采购审核、资料保密和故障上报指引。 |
| `samples/shared/learning.md` | 用于根据 Excel 基础、每周可用时间与 SQLite 环境制定四周 SQL 销售分析学习计划。 |
| `samples/shared/travel.md` | 用于核对出差航班、会议、机场交通和住宿安排中的时间冲突与缺口。 |
| `samples/shared/team_event.md` | 用于在人数、时段、交通预算和天气限制下比较三种团队活动方案。 |
| `samples/shared/local_rules.md` | 用于识别样例文件整理任务的操作范围、禁止删除源文件及产物目录约束。 |

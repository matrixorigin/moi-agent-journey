# moi-agent-journey

在真实 QA 部署上测试 MOI 智能体的任务完成质量、效率和稳定性。

## 执行方式

`Agent journeys QA` 每六小时运行一次，也支持手动运行。PR 和合入 main 不触发真实 QA；PR 只运行 `Offline tests`，验证执行框架与验收器，不读取 QA 凭据。定时与手动入口需要工作流先合入 main；真实 QA 使用 GitHub `qa` environment 的 `MOI_BASE_URL` 与 `MOI_PAT` secrets。

配置入口是 [suites.json](suites.json)：

| 工作区 | 当前内容 | 最大并发 |
|---|---|---:|
| journey-1 | 18 个文档与办公任务 | 9 |
| journey-2 | 空 | 3 |
| journey-3 | 空 | 3 |
| journey-4 | 空 | 3 |
| journey-5 | 空 | 3 |

五组可以同时运行；按当前并发配置，全部配置 case 后最多 21 个 case 同时调用 QA。空组显示 `empty`，不产生虚假通过结果，也不计入成功率分母。每个 case 单独使用一个标准 `ubuntu-latest` runner，失败不会取消同组其他任务。全局 concurrency group 防止整批运行相互重叠；后续批次排队等待，最多保留 100 个等待中的运行。连续提交过多可能造成积压并推迟定时回归。

每个 case 的 **QA 总预算为 3600 秒**，覆盖所有用户追加对话和 π-Bench 语义评分。主任务超时记为 timeout；语义评分阶段超时或评分结果无效记为验收器错误。两者都会取消已知未结束任务、补采事件并归档会话，最终取证有独立的 100 秒预算。主任务耗时与语义评分耗时分开统计；材料准备和确定性验收另计；GitHub job 的 135 分钟上限给安装、准备、QA、验证和上传留出时间。长任务统一使用正式的 message/stream SSE 协议，先取得 task ID 并实时落盘；随后核对持久化任务和事件。业务请求和模型任务不自动重试，保留第一次失败证据。

## 判断是否成功

- **任务结果**：`passed`、`failed`、`timeout`。QA 报错、缺少交付物、文件验收不通过都会如实计入失败，工作流继续收集其余结果。
- **执行框架结果**：`ok`、`error`。材料准备失败、验证器异常、语义评审失败或返回无效结果、事件或 trace 采集不完整、runner 被终止、case artifact 丢失都会让工作流报错。

完成状态为 `completed`、HTTP 200、模型声称已完成都不足以判成功。每个 case 的 job 只有在任务验收通过、执行和取证正常时才显示绿色；任务未通过、超时或执行/验收/取证出错均显示红色。红色的具体类别和原因见该 job 的 `Case result and failure details` 步骤：执行或验收出错表示无法确认结果，不等于智能体任务失败。其他 case 仍继续运行，证据和总报告仍会上传。本批有未通过或错误时工作流显示失败；成功率分母固定为本次配置的所有 case，缺失结果不会让成功率上升。

**判据校准尚未完成。** J1-02、J1-07、J1-10、J1-13 和 J1-15 已发现部分上游检查与任务或材料不一致；应结合具体判词和原始产物复核。J1-14 对 Word 和邮件的部分自然语言一致性只能做有限检查。J1-18 的上游 C08 要求引用具体旗舰系列，但这一要求未在任务和会议纪要中明确，需要单独复核。当前通过率是检查器的结果，不能直接当作智能体能力得分。

## Journey-1 的 18 个 case

| 编号 | 任务 | 主要检查 |
|---|---|---|
| J1-01 | 合同接受全部修订 | 插入/删除语义、旧条款清除、可编辑 DOCX |
| J1-02 | 长报告排版 | 字体、段落、层级编号、图表题注 |
| J1-03 | PDF 台账录入 Word | 记录准确、表格与行高、原模板内容 |
| J1-04 | PDF 与 Excel 人员差异 | 删除 ID、字段变更、新旧值和类型 |
| J1-05 | 论文匿名化 | 作者/机构/身份标识清除，正文和自引保留 |
| J1-06 | 异构子表汇总 | 混凝土体积、总重及明细重量 |
| J1-07 | LBO 财务模型修复 | 业务公式关系、实际重算、非目标单元格保护 |
| J1-08 | PDF/Excel 人口收入联表 | 原生透视表、源数据、分组和聚合 |
| J1-09 | 库存补货预测 | 销售窗口、交期、安全库存和补货日期 |
| J1-10 | 流程图重建 PPT | 原生可编辑节点与连线、指定文本修改 |
| J1-11 | PPT 内嵌 Excel 汇率修改 | 正反汇率、原公式与其他内容保护 |
| J1-12 | Excel 数据追加到 PPT | 原生表格、折线图和内嵌数据一致 |
| J1-13 | 按 Word 要求修订 PPT | 页面布局、图片、字体和动画 |
| J1-14 | 发票应付控制包 | 授权证据、金额/日期、跨文件一致、离线草稿 |
| J1-15 | 配送税率跨文件更新 | 原生公式、配置明细、处理器变更回放 |
| J1-16 | 百份混合文件归档 | 100 篇 PDF + PPTX/DOCX 的内容分类和逐文件哈希 |
| J1-17 | 两项目采购修订与合并 | 三个独立会话、八项立即采购、延期和备选、真实产物溯源 |
| J1-18 | 品牌研究到联名方案 | 三个独立会话、研究/规则/方案、语义约束和真实产物溯源 |

18 个 case 共包含 22 个上游任务实例。每个目录的 `case.json` 记录固定来源、输入 URL/SHA256、对话、交付清单、判据和检查器来源。153 份输入已在初始配置时实际下载核验；材料和上游检查器运行时从固定来源获取并再次校验 SHA256，不提交大体积二进制材料或上游完整代码。J1-16 输入约 231 MiB；下载源不可用或字节变化会明确报准备错误。

前 16 个上游单任务改为“先核对与计划 → 用户确认执行 → 用户要求回读复核”的 MOI 多轮版本；原始请求保存在 `instruction.txt`。最后两个保留三任务链，任务间开新会话，通过上传前序真实产物显式传递材料，最终 `source_lineage.json` 必须与前序产物哈希一致。这是 MOI 回归改编，不宣称运行原 benchmark 的官方完整协议或产出可与其排行榜直接比较的分数。

验收保留上游逐条评分依据，并要求业务检查全部通过。J1-02 的标题从原始报告读取，校验第一页位置及标题和摘要格式，其他格式仍沿用上游检查；J1-03 从实际 PDF 推导并核对每笔交易与余额，检查模板原有记录和 Word 表格；J1-06 按各子表的标签寻找实际数值并重算工作簿；J1-12 检查新表格、折线图及其嵌入工作簿是否覆盖输入的全部年份和指标；J1-14 核对四张发票的来源、决定和草稿，对可识别的 Word 表格检查跨文件一致性，并通过修改工作簿副本中的金额、阈值、阻断名单和到期日验证公式联动。这些 case 的上游原始判词也保留在 `upstream_result.reference_upstream_result`，但不作为适配检查的最终判定。J1-14 上游检查器可能因工作表布局提前退出，这种情况会在参考结果中标记，最终判定由适配检查给出。无法识别的表格布局会报验收器错误，不会当作通过。J1-08 将源 PDF 中明确包含的地区纳入有效值，并单独记录不适用的透视表检查；J1-09 对不需要补货的产品接受空白或 `N/A` 日期，仍逐项核对需要补货的日期，上游原判词和适配后的检查均保留。其他 OfficeVal 和 SkillsBench 检查使用固定版本的上游检查器。EvoCowork 检查全部业务单位和扣分；LBO 在验收副本中显式配置迭代计算，验证原始数据和变更驱动下的业务公式关系。Office 交付物先独立解析，再由 LibreOffice 实际打开并导出 PDF，渲染证据一并保存；验收容器使用 init 回收转换进程的子进程；转换工具崩溃或没有产出时标记验收器错误，不据此推断智能体失败。自动结构/渲染检查仍有视觉审美覆盖边界。外部链接仅检测可识别的危险目标，不判断远程网站本身是否安全。

π-Bench 额外使用同一模型在独立 Momo 会话中逐条语义评分，按 A2A 投影约定读取末尾的最终答复，过程文本继续完整留档，并要求通过项提供实际交付文件内的原文证据。语义评分仍可能有模型误判，完整判据、答卷、判词与评分会话 trace 都保留，便于复核。

验证在无网络、无 QA 凭据、只读输入/交付物、受 CPU/内存/进程限制的 Docker 内执行。J1-15 会执行智能体生成的处理器做回放；该代码不能在有 QA 凭据的宿主 runner 上执行。每次工作流只构建一次验收镜像，以 1 天有效的 `judge-image-<run_id>-attempt-<n>` artifact 分发给各 case；各 runner 校验镜像的哈希和代码版本后独立运行容器。镜像不包含 QA 凭据或 case 数据。

## 下载与分析证据

点开某个 `cases (J1-XX)` job，展开 `Case result and failure details` 步骤，可查看该 case 的结果、原因、耗时和逐轮 trace ID；同一份摘要也出现在运行的 Summary 中。若 runner 在生成报告前被终止或该步骤未执行，需查看出错步骤和总报告中的缺失结果。进入 Actions 本次运行底部的 **Artifacts**，下载 `journey-run-<run_id>-attempt-<n>`。每个 case 完成后也会上传 `case-J1-XX-attempt-<n>`，其中包含 `case-summary.md` 和详细证据，长任务尚未全部结束时可以下载已完成的证据。重跑时选择 **Re-run all jobs**；汇总只接收本次 attempt 的结果，防止旧结果覆盖新失败。证据包 **保留 1 天**；如需更久，在两个证据上传步骤中同步修改 retention-days。

统一包包含：

```text
summary.json                  # 全批及每组成功率、每个 case 的详细结果
summary.csv                   # 任务结果、耗时、trace ID、失败原因
summary.md                    # 与 GitHub Summary 一致的报告
cases/J1-XX/
  case-summary.md             # 单个 case 的结果、原因、耗时和逐轮 trace ID
  result.json                 # case 规格哈希、代码版本、所有轮次 task/trace ID、耗时、失败原因和观察到的工具错误
  publication.json            # 公开包完整性；隔离文件的路径、哈希与原因
  preparation.json            # 输入与 oracle 的来源及哈希，或准备失败原因
  dialogue.jsonl              # 实际用户输入和附件引用
  http.jsonl                  # 脱敏后的产品请求/响应和耗时
  events.jsonl                # 按 task ID + seq 记录公开任务事件
  stream.jsonl                # SSE 实时帧，先记录 task ID 再等待长任务
  tasks/<task_id>/
    runtime.json              # 最终运行时详情与 trace ID
    a2a.json                  # 最后一次持久化 A2A 任务结果
  artifacts/<conversation_id>/
    inventory.json            # 实际发布产物清单、文件 ID 和哈希
    <artifact_id>-v<version>/  # 下载的产物
  sessions/<session_id>/
    reply.json                # π-Bench 的规范最终答复，独立于过程文本
    outputs/                  # 解包后的实际交付文件
    verification/             # 逐项判词、验证器日志和渲染 PDF
  semantic-verdicts.json       # π-Bench 独立语义评分
```

采集 QA 产品公开事件 API 返回的全部事件，包括可用的 thinking/推理片段、文本增量、工具调用、工具结果、生命周期和产物事件。服务端私有 rollout 不在公开 API 范围；某个模型没有输出 thinking 时不会伪造。公开事件序号可以因私有事件而有间隔，按 API 游标读取，不能以序号连续性判断遗漏。每轮结束和取消后再读取持久化事件；结果中明确记录 capture_complete。

`observed_tool_errors` 摘录公开事件中明确失败的工具调用、工具输出中明确标记失败的结果，以及带 HTTP 错误状态和代码的结构化结果，并保留 task ID、事件序号与 request ID。这些只是运行中观察到的错误；智能体可能随后恢复，不能单凭它们认定最终失败根因。完整原文仍在 `events.jsonl`。

每个结果记录工作流 commit 和包含模型、超时配置的 case 规格 SHA-256。修改题目或验收器后的结果应按对应版本比较，不能用新判据倒推旧任务是否完成。

不把凭据放进提示词或材料，HTTP 认证头不写日志。文本记录脱敏；上传前扫描文件原始字节，并递归检查 ZIP/Office 内容。发现已知凭据、损坏或无法完整检查的 ZIP、分卷及符号链接会被隔离；publication.json 记录路径、哈希和原因，汇总报告显示隔离数量。扫描不能保证识别其他二进制文件中压缩或编码的敏感信息。原始任务判定保持不变，未隔离的 trace、事件和结果继续上传；受认证保护的 QA 文件引用仍在 inventory.json 中，可回 QA 诊断。公开仓库的 artifact 也应视为公开数据：本套只使用公开 benchmark 材料，请勿接入客户私有文件。会话归档后仍保留在 QA，输入文件和产物也保留供诊断；周期运行会占用 QA 存储，后续需根据保留策略清理这些专用工作区。

## 本地验证

离线执行框架测试只依赖 Python 3.10+ 标准库：

```sh
python3 -m unittest discover -s tests -v
python3 -m journey.catalog plan journey-1
```

Office 验收器的可编辑文件正反例在无网络 Docker 中运行，离线 CI 会自动执行。也可在构建验收镜像后执行：

```sh
docker run --rm --init --network=none --read-only --cap-drop=ALL --security-opt=no-new-privileges --memory=3g --cpus=2 --pids-limit=256 --tmpfs /tmp:rw,size=1g,mode=1777 --mount "type=bind,src=$PWD/tests_judge,dst=/tests_judge,readonly" --entrypoint python moi-journey-judge -m unittest discover -s /tests_judge -v
```

完整任务需要 Docker 和 QA 凭据，从仓库外的私有来源注入 `MOI_BASE_URL` 与 `MOI_PAT`：

```sh
python3 -m journey.catalog prepare J1-01
# 从仓库根目录构建并执行
docker build -t moi-journey-judge -f judge/Dockerfile .
python3 -m journey.run_case J1-01
python3 -m journey.validate J1-01
python3 -m journey.report case J1-01
python3 -m journey.publish
```

原 `python3 -m journey.hello` 冒烟入口及其测试仍保留，模型变量为 `MOI_MODEL`。

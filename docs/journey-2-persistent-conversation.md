# J2-132：跨 Action 的同一持久会话

此用例每次复用 journey-2 MOMO 的同一个真实会话，300 秒预算覆盖本轮全部请求。其他功能 case 继续新建、归档独立会话。

会话 UUID 由目标服务地址、工作区 ID、智能体身份、case ID 和 `long-lived-v1` 稳定生成，不包含 GitHub run/attempt/branch。只有按该 ID 查询明确返回 404 时才新建；身份不一致、已归档、鉴权/网络故障或正在执行旧任务均明确报告，不更换会话、不清空历史、不重发任务。不同服务地址的 QA/local-deploy 使用不同会话。

每次运行三轮：

1. **历史追问**：从真实用户消息历史读取上次检查点作为验收标准，但不把编号放入新提示词，让智能体回答上轮编号。首次新建时进行初始化，报告明确显示 `bootstrap`，不计作已验证跨运行连续性。已有会话缺少检查点记为 `missing_checkpoint` 失败，不冒充首次初始化通过。
2. **追加任务**：按实际历史检查点序号循环：普通计算 → 新 CSV → 新图片 → PDF Skill → 指定知识库。文件内容与图片数字每轮重新生成，答案不放入提问；Skill 必须有真实成功调用；知识库使用现有合成知识库准备/检索/引用验收器。知识库仅在对应轮次创建，本轮任务结束后清理。
3. **新检查点**：把新的随机编号发到同一会话，要求原样确认，再从用户历史核对本轮 task 和检查点确实存在。即使历史回忆答错，也继续保存本轮证据和新检查点，原失败保持为失败。

历史编号是公开测试数据，不是账号或密钥。标记保存在 QA 的真实消息里，不依赖 runner 磁盘、GitHub cache 或下载上次 artifact。超过一页的历史按分页向前寻找最后一个检查点，历史响应异常直接报告。

本轮 `result.json` / 单 case Markdown 包含固定 conversation ID、是否新建、序号、模式、跨运行回忆结果及保留状态。stream、公开 thinking/工具事件、task/trace ID、耗时、工具错误沿用现有录制与统计；`persistent/` 保存检查点来源、当前用户历史和本轮新输入。对话未开始时没有 agent trace；HTTP 请求 trace 仍保留在 `http.jsonl`。

结束时仅取消、补采本轮已确认身份的任务，保留会话为 active。SSE 响应丢失时通过本轮唯一 prompt、历史 seq 和 task ID 恢复，不用旧会话中的“未知任务”推断归属，不触碰历史任务。用户上传的测试附件作为长期对话材料保留在 QA；本轮临时知识库清理结果单独记录。这里测试同一会话的持续运行，不等于产品的跨会话记忆功能。

GitHub 的全局 concurrency group 已串行化整批 Action；J2-132 每批只有一个 matrix job，其余 journey-2 case 仍可并发 9。不要同时在本地和 Action 对同一个服务运行此 case；服务的会话受理锁及 busy 结果会暴露冲突，测试不会自动切换会话掩盖问题。

本地复现：

```sh
python3 -m journey.catalog prepare J2-132
python3 -m journey.functional_case J2-132
python3 -m journey.report case J2-132
```

连续执行两次可验证同一 conversation ID 和历史回忆；五次可走完第一轮模式轮换。每次使用独立的 results/work 目录保存证据，首次失败不被后续结果覆盖。删除 QA 上这个专属会话会使下次明确记录新建；同一 ID 的历史意外消失会暴露在初始化状态与历史快照中。

[2026-11-03 的真实 QA 实测](journey-2-persistent-qa-2026-11-03.md)记录了三次后续运行的历史连续性、CSV/图片/Skill 的通过，以及知识库准备阻塞和后续独立清理。

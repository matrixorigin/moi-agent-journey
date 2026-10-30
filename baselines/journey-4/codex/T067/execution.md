# T067 执行记录

## 读取来源

- 本 case `AGENTS.md`、`TASK.md`：任务范围和输出约束。
- `<local-RTK-instructions>`：shell 必须以 rtk 开头。
- `samples/T067/task.md`：指定读取 PNG 并生成中文 SOP。
- `samples/shared/flowchart.png`：通过 `view_image` 实际打开并目视读取，图片尺寸 1200 × 900。

未读取其他 SOP、其他 case、父目录答案、历史或记忆。

## 实际执行及回执

1. `rtk proxy cat AGENTS.md TASK.md`：退出码 0，读取任务规则。
2. `rtk proxy cat <local-RTK-instructions> samples/T067/task.md`：退出码 0，确认任务与 RTK 规则。
3. `view_image` 打开本 case 的 `samples/shared/flowchart.png`：成功显示图片。读取到采购流程四步、批准后 export and archive、拒绝后 revise quote，以及明确缺失的 unavailable shipping 分支。
4. `rtk proxy python3 - <<'PY' ... PY`：实际创建 `results/T067/result.md` 与本记录。随后同一脚本检查两文件存在、非空，并核对正文包含四个主步骤、两个审批分支和无法配送异常。

## 转写口径及限制

- 依据图中编号确定先后顺序；未自行增加采购下单、付款或收货流程。
- “Approved / Rejected” 分别译为批准／拒绝；“unavailable shipping” 译为无法配送／无可用运输方式。
- 图中未说明拒绝修订后的回流节点，因此保留为待定义，不擅自补成原图规则。
- 图中明确缺失的异常与由节点推导出的其他缺口已区分。流程责任、审批和归档细则仍需业务方定义；不影响本次转写交付。
- 无数值计算，无外部查询，无实际采购、发送或发布操作；未执行任何审批。

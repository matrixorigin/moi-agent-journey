# 执行记录

## 读取来源

- `AGENTS.md`：本 case 执行边界与 RTK 要求。
- `<local-RTK-instructions>`：shell 命令须以 rtk 开头。
- `TASK.md` 与 `samples/T045/task.md`：起草 5 条社交内容，角度不同、事实一致，只起草。
- `samples/shared/product.md`：唯一产品事实来源；没有访问外部网站、其他 case、历史、记忆或其他产品答案。

## 实际执行

1. `rtk read <local-RTK-instructions>`：退出码 0，读取 RTK 规则。
2. `rtk read AGENTS.md TASK.md`：退出码 0，读取任务与目录限制。
3. `rtk read samples/T045/task.md samples/shared/product.md`：退出码 0，读取产品事实与交付要求。
4. `rtk proxy python3 -c ...`：退出码 0，实际创建交付目录，并以 UTF-8 写入 `result.md` 和本记录。正文包含 5 条完整草稿。
5. `rtk read results/T045/result.md results/T045/execution.md`：退出码 0，实际回读两个文件，核对草稿数量、事实范围与限制。
6. `rtk proxy python3 -c ...`：将“试用评估”改为“业务评估”，避免暗示未提供的试用政策；并检查最终文件正文有 5 条内容。

## 数据口径与事实核对

- 试点样本为 20 单；平均耗时由 120 分钟降至 60 分钟。降幅若按原耗时计算为 `(120-60)/120=50%`，仅适用于所述试点，未推广到所有客户。
- 上传方式为人工上传 CSV/XLSX；未写成自动采集或自动同步。
- 能力限于含税运费对比、来源引用、经理审批、导出报告。
- 保留不支持自动下单与 ERP 自动写回的边界；未宣称免费或无限免费。
- 五个角度依次为采购比较、财务出处核对、IT 能力边界、经理审批、试点效率观察。

## 限制与未完成项

- 输入没有指定具体平台、字数、价格、网址或试用政策，因此提供可适配社交平台的中文草稿，未虚构这些信息。
- 未公开发布、未发送消息；无任务要求范围内的未完成项。

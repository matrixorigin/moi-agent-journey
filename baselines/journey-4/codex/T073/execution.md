# 执行记录

## 来源
- 本case的AGENTS.md与TASK.md，以及其引用的公共命令规范 `<local-RTK-instructions>`。
- `samples/T073/task.md`：交付要求与输入位置。
- `samples/shared/budget.csv`：读取3条部门记录；字段为department、budget、actual、note。使用utf-8-sig处理文件BOM。

## 实际执行
1. 通过 `rtk read <local-RTK-instructions>`、`rtk read <case-root>/T073/AGENTS.md` 和同目录的 `TASK.md` 读取约束，均退出0。
2. 在case目录执行 `rtk read samples/T073/task.md` 和 `rtk read samples/shared/budget.csv`，均退出0，原始预算/实际分别为研发10000/12000、市场20000/15000、行政5000/5000。
3. 实际执行 `rtk proxy python3 - <<'PY'` 内联Python程序，用csv.DictReader读取输入，Decimal计算 `(actual-budget)/budget*100`，按两位小数输出。程序写入result.md与calculation.json，退出0。
4. 实际计算回执：研发差异2000、20.00%；市场差异-5000、-25.00%；行政差异0、0.00%；合计预算35000、实际32000、差异-3000、-8.57%。执行断言 `(预算合计,实际合计,差异合计)==(35000,32000,-3000)`，未触发异常。
5. 本记录通过第二次 `rtk proxy python3 - <<'PY'` 写入，并回读三个产物确认存在、UTF-8可读且非空；下方为该命令实际回执对应的核对内容。

## 口径与限制
差异＝实际－预算，正值为超支，负值为低于预算；合计比例按合计预算加权计算。市场备注明确为活动延期，因此不将其认定为永久节约。备注是解释来源，没有其他凭证可验证新增环境费用或延期后执行时间。源数据未提供金额单位、预算期间或后续承诺支出，未自行补充。

未完成项：无。所有产物仅写入本case的results/T073。

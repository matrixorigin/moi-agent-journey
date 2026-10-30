# 实际执行记录

工作目录：`<case-root>/T026`。

## 来源与实际读取

- 本 case 的 `AGENTS.md`、`TASK.md`：任务边界与交付要求。
- `<local-RTK-instructions>`：要求 shell 命令以rtk开头。
- `samples/T026/task.md`：按注册月、按用户去重，明确只能度量付费留存。
- `samples/shared/cohort.csv`：唯一业务数据源，6行记录、3个字段。
- 未查询网络、未读取其他case、历史或记忆、未委派。

## 实际命令与回执

所有命令均在上述工作目录执行。

1. `rtk proxy cat AGENTS.md TASK.md`，exit 0；读到本任务范围和输出目录。
2. `rtk proxy cat <local-RTK-instructions> samples/T026/task.md samples/shared/cohort.csv`，exit 0；读到任务及6行原始记录。
3. `rtk proxy python3 -c 'from pathlib import Path; p=Path("samples/shared/cohort.csv"); print(repr(p.read_bytes()))'`，exit 0；核对文件含UTF-8 BOM及行分隔形式。
4. `rtk proxy mkdir -p results/T026`，exit 0。
5. `rtk proxy python3 - <<'PY' ... PY`，exit 0；实际写入 `analyze.py`，代码完整保存在该文件中。
6. `rtk proxy python3 results/T026/analyze.py`，exit 0；实际生成CSV与JSON，打印下述核对数据。
7. `rtk proxy python3 - <<'PY' ... PY`，实际写入本记录及 `result.md`。

## 计算与实际输出

使用Python标准库csv按UTF-8-sig读入，注册月按用户集合去重，每个(cohort,月龄)付费人数亦用用户集合去重。检查同一用户注册月一致、付费月不早于注册月。月龄按自然月差计算。观察上界仅取输入最大付费月，不声称数据完整。

实际打印：input_rows=6，unique_users=4，unique_user_payment_months=6，duplicate_user_payment_rows=0，inferred_last_month=2026-10，escaped_newlines_normalized=false。

结果依次为：8月M0 2/2=1.0、M1 1/2=0.5、M2 0/2=0.0；9月M0 2/2=1.0、M1 1/2=0.5、M2=null（未观察）。脚本中计数及结果断言均通过。

## 限制与未完成部分

没有用户全量注册表、活跃行为、收入金额或数据完整截止日，因此无法确认分母覆盖全部注册用户，也无法得出活跃留存、收入留存、完整10月留存或最终流失率。10月数据按当前样本暂计，11月用N/A。任务所要求的样本付费留存计算及交付均已执行。

## 最终文件核验

已实际重新读取生成的CSV、JSON与正文，CSV共6个cohort/月龄单元，JSON去重用户数4，未观察单元为空且状态not_observed。文件存在且非空：result.md、execution.md、analyze.py、cohort_retention.csv、audit.json。验证命令为 `rtk proxy python3 - <<'PY' ... PY`，断言通过。

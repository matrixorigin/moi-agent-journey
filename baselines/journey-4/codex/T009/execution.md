# T009 执行记录

## 读取来源与约束

仅读取本 case 的 `AGENTS.md`、`TASK.md`、`samples/T009/task.md`、`samples/shared/funnel.csv`，以及 AGENTS.md 引用的公共命令规范 `<local-RTK-instructions>`。未读取父目录答案、其他 case、历史或记忆，未委派、未访问客户资料或执行外部写入。

## 实际命令与回执

所有 shell 命令均通过 `rtk` 开头执行，工作目录为本 case 根目录。

1. `rtk proxy cat AGENTS.md TASK.md`：退出码 0；确认任务为计算相邻步骤流失率、定位主要流失环节、提出三个可验证假设，交付到 `results/T009`。
2. `rtk proxy cat <local-RTK-instructions> samples/T009/task.md samples/shared/funnel.csv`：退出码 0；读取命令规范、任务和原始数据。
3. `rtk proxy python3 - <<'PY' ... PY`：实际运行 Python，使用 `Path.read_text(encoding='utf-8-sig')`、`csv.DictReader` 读取五行数据，逐对计算流失人数、流失率和转化率。原始文本 repr 显示正常换行；兼容字面转义换行的替换对本文件无影响。输出如下：

```text
访问→注册: lost=600; loss_rate=60.00000000%; conversion=40.00000000%
注册→首次上传: lost=280; loss_rate=70.00000000%; conversion=30.00000000%
首次上传→生成报告: lost=30; loss_rate=25.00000000%; conversion=75.00000000%
生成报告→分享报告: lost=75; loss_rate=83.33333333%; conversion=16.66666667%
end_to_end: 0.015
```

4. `rtk proxy python3 - <<'PY' ... PY`：实际创建 `results/T009`，从 CSV 重新计算表格并写入 `result.md` 和本执行记录；脚本对人数单调性、各段流失合计、最终文件存在且非空进行核对，输出见下。

## 计算口径与核对

前一步为每段分母；整体指标以访问 1,000 人为分母。五步人数为 1,000、400、120、90、15，均单调不增；四段流失合计 985 = 1,000 − 15。整体转化率 1.50%，整体流失率 98.50%。比例按未舍入值计算后展示两位小数。

## 限制与未执行部分

输入未提供统计周期、同队列证明、事件定义和用户级数据，因果解释仅作假设。已实际生成要求的两份 Markdown 文件；没有实施产品修改、埋点或 A/B 实验，这些不属于本次分析的执行范围。

最终核对输出：`result.md` 与 `execution.md` 均存在且非空；`verified: 5 steps; loss sum=985; overall conversion=1.50%`。写入与核对脚本退出码 0。

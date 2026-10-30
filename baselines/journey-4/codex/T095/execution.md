# 执行记录

## 来源与约束

仅读取本 case 的 `AGENTS.md`、`TASK.md`、`samples/T095/task.md`、`samples/shared/learning.md`，以及用户明确要求的公共指令 `<local-RTK-instructions>`。未读取其他 case、历史或评分材料，未委派。

输入事实：会 Excel、不会编程；目标四周掌握 SQL 订单分析；SQLite；通常每周 4 小时，第 2 周仅 2 小时；期望实际销售分析项目。样例未提供销售数据，本次交付为学习计划，数据和项目为计划中的学习产出。

## 实际执行

1. `rtk read <local-RTK-instructions>`：退出码 0，确认 shell 必须使用 rtk。
2. 在当前 case 执行 `rtk read AGENTS.md TASK.md`：退出码 0，读取任务和交付要求。
3. 执行 `rtk read samples/T095/task.md samples/shared/learning.md`：退出码 0，获取学习基础、目标、时长及环境。
4. 执行 `rtk proxy python3 - <<'PY' ... PY`：实际创建 `results/T095/result.md` 和本记录，内容为四周计划及执行说明。

## 时间计算与限制

第 1 周 30+45+60+60+45=240 分钟；第 2 周 60+60=120 分钟；第 3 周 45+45+60+60+30=240 分钟；第 4 周 30+90+60+45+15=240 分钟。合计 840 分钟，即 14 小时。

计划中四周各有练习、检查点及交付，第二周严格限制两小时。没有创建或运行学习项目数据库；没有学员实做结果，亦不将计划写成已执行结果。无外部资料引用，无工具缺失阻碍，无需联网。

## 写入回执

生成命令退出码 0；输出确认 `result.md` 6222 字节、`execution.md` 初次写入 1501 字节，均可 UTF-8 读回。时间合计输出 `14.0` 小时。此后补写本回执，记录文件体积随之增加。

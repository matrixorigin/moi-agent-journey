# 执行记录

## 来源与范围

读取本case的AGENTS.md、TASK.md、samples/T097/task.md、samples/shared/team_event.md，以及AGENTS.md指定的公共规则 <local-RTK-instructions>。未读取其他case、历史、记忆或其他产品答案。

唯一业务数据源为 samples/shared/team_event.md：10人，2026-11-20 14:00–18:00，上海，预算3000元含交通；三方案费用、A容量12人、B雨天取消、C最早17:00入场均来自该文件。未查询外部商家或实际档期，未预订或发送消息。

## 实际执行

以下shell命令均已执行，读取命令退出码为0：

1. `rtk read <local-RTK-instructions>`
2. `rtk read AGENTS.md`
3. `rtk read TASK.md`
4. `rtk read samples/T097/task.md`
5. `rtk read samples/shared/team_event.md`
6. `rtk proxy python3 - <<'PY' ... PY`：实际运行Python，按10人计算三方案总价及余量，写入 result.md、execution.md，并重新读取result.md核对关键金额及文件大小；输出回执见下。

## 计算与写入回执

{
  "A": {
    "total_yuan": 2300,
    "remaining_yuan": 700
  },
  "B": {
    "total_yuan": 1600,
    "remaining_yuan": 1400
  },
  "C": {
    "total_yuan": 3150.0,
    "remaining_yuan": -150.0
  }
}

A：1200+80×10+300=2300；3000−2300=700。
B：600+20×10+800=1600；3000−1600=1400。
C：250×10×1.1+400=3150；3000−3150=−150。
C可用起点17:00晚于需求14:00，和需求时段仅有17:00–18:00重叠。

## 限制

输入未提供具体场地地址、真实档期、B/C承接人数、交通覆盖范围或完整报价条款。已在正文列为待确认，不将未知项目当作已确认，也未虚构报价。比较和计算已完成；档期、补充费用及商家条款未核实，预订未执行（按任务要求）。

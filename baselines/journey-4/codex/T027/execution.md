# 执行记录

## 来源及边界

仅分析本 case 的 `samples/shared/daily.csv`。已读取本 case `AGENTS.md`、`TASK.md`、`samples/T027/task.md`，以及其要求的公共工具规则 `<local-RTK-instructions>`。未读取历史、记忆、其他 case、其他产品答案或评分；未访问外部业务系统；未委派。

## 实际命令与回执

所有命令在本 case 根目录运行，均以 `rtk` 开头，退出码均为 0。

1. `rtk read AGENTS.md`：要求只执行当前任务、shell 使用 rtk、禁止跨 case 读取与委派。
2. `rtk read TASK.md`：确认任务为 T027 经营异常定位及交付位置。
3. `rtk read <local-RTK-instructions>`：确认 `rtk proxy <cmd>` 可运行原始命令。
4. `rtk read samples/T027/task.md`：确认唯一输入为 `samples/shared/daily.csv`。
5. `rtk ls samples/shared`：查看样例目录文件名；未读取任何其他业务样例内容。
6. `rtk read samples/shared/daily.csv`：读得字段 date/product/region/customer/revenue，4 行收入为 100、100、1000、100。
7. `rtk proxy python3 - <<'PY' … PY`：已实际执行 Python 标准库 csv、collections、json、hashlib、pathlib；按 UTF-8 BOM 编码读 CSV，聚合日期及三个维度，计算前两日日均、增量与贡献，写出 result.md 和 calculations.json。实际输出为：

```text
source_sha256: ea5ddee79005014999c9f9d0d9aad91d88c4483f7458ac83baa989653096ccce
rows: 4
duplicate_dimensions: 0
daily_revenue: 2026-10-20=100; 2026-10-21=100; 2026-10-22=1100
baseline_daily_mean: 100
oct22_delta: 1000
oct22_growth_pct: 1000
A / 华东 / C01: baseline=100; current=1000; delta=900; contribution=0.9
B / 华南 / C02: baseline=0; current=100; delta=100; contribution=0.1
Verified: 4 rows; daily totals 100/100/1100; each dimension reconciles to +1000; result.md and calculations.json written.
```

8. 本次 `rtk proxy python3 - <<'PY' … PY` 写入 execution.md，并检查三个交付文件存在且非空；回执见末尾。

## 核对与限制

实际断言检查：行数为 4、所有值非空、日汇总为 100/100/1100、总增量为 1000，每个维度的增量合计均为 1000。重复组合计算值为 0。三个维度是同一批收入的不同切面，不叠加。

业务原因无法从现有字段确定；没有订单、单价、数量、利润或长期历史。只做样本内描述性异常定位，未虚构原因或声称完成业务凭证核查。无其他未完成的交付项。

文件验证回执：result.md、execution.md、calculations.json 均存在、UTF-8 可读取且非空。

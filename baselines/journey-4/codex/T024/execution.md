# T024 执行记录

执行范围仅本 case；没有读取其他 case、历史/记忆、其他产品答案或评分。没有委派、发信、公开发布或修改源数据。

## 来源和实际读取命令

以下命令均实际执行且返回 exit_code 0：

```sh
rtk read AGENTS.md
rtk read TASK.md
rtk read <local-RTK-instructions>
rtk read samples/T024/task.md
rtk read samples/shared/customers_crm.csv
rtk read samples/shared/customers_billing.csv
rtk read <local-path>
```

技能文档为必要工具文档；工具返回内容被截断。本任务仅对 CSV 做只读分析，以 Markdown 提交清单，没有创建或编辑工作簿。

## 实际计算与写入

实际执行 `rtk proxy python3 - <<'PY'` 内联 Python 脚本（本目录为工作目录），exit_code 0。使用标准库 csv.DictReader 和 utf-8-sig 读取源 CSV；Decimal 汇总余额；按 strip().lower() 后的非空邮箱进行双侧唯一匹配；对未匹配 billing 仅提取名称相同的 CRM 候选。脚本实际生成 result.md、verification.json。

脚本回执：

```json
{"crm_rows":3,"billing_rows":4,"matched_pairs":[["C01","B01"],["C02","B02"]],"unmatched_crm":["C05"],"pending_billing":["B03","B04"],"balance_total":"4200","matched_balance":"3000","pending_balance":"1200","coverage_and_balance_assertions":"completed"}
```

已执行断言：匹配数加待确认数等于 billing 总数；已匹配 CRM 数加未匹配数等于 CRM 总数；匹配余额加待确认余额等于总余额；匹配账号集合为 B01/B02，待确认账号集合为 B03/B04。未以名称强行关联 C05 与 B03/B04。

最终使用第二次 `rtk proxy python3 - <<'PY'` 写入本执行记录，并读取 result.md 与 verification.json，核对文件存在、内容非空和数值回执；输出文件尺寸及摘要。该命令的回执见实际工具记录。

## 限制与未完成

输入没有币种，余额按原单位汇总；没有额外唯一业务标识及邮箱变更证明。C05/B03/B04 的身份关系仍需人工核实。本次交付匹配和待确认清单，不执行 CRM/billing 实体合并。

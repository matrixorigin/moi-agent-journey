# 双语一致性审校

以所给中文为本次一致性修订基准；这只解决两种语言的对应关系，不代表已独立验证业务数据或产品能力。

## 逐条问题与修订

| 序号 | 类型 | 原文对应与问题 | 修订 |
|---|---|---|---|
| 1 | 数字：试点规模 | 中文“试点20单”，英文“Pilot of 200 orders”，英文扩大为中文的10倍。 | 改为“Pilot of 20 orders”。 |
| 2 | 数字：耗时及效果 | 中文“从120分钟降到60分钟”，英文“from 120 to 30 minutes”，终值错误。按中文计算减少60分钟、降幅50%；按错误英文则减少90分钟、降幅75%，夸大了改善幅度。 | 改为“time decreased from 120 to 60 minutes”。 |
| 3 | 数字：保留期限 | 中文“保留12个月”，英文“Retain data for 6 months”，英文期限少6个月，仅为中文的一半。 | 改为“Retain data for 12 months”。中文未明示保留对象，英文使用data；此处保留英文已有对象，不扩大为订单或报价等特定数据。 |
| 4 | 漏译及能力承诺反转 | 中文“不支持自动下单”，英文“Supports CSV and automatic ordering”，丢失否定含义并反向承诺支持自动下单。 | 改为“Supports CSV. Does not support automatic ordering.” |
| 5 | 术语不一致 | 已约定“报价比较=quote comparison”，后文却使用“quotation contrast”。 | 所有表示“报价比较”的位置统一使用“quote comparison”。 |

漏译检查：没有发现整条中文信息完全缺失；关键漏译是“不支持”的否定含义缺失，且造成能力承诺反转。“支持CSV”与“Supports CSV”一致，无需修改。起始耗时120分钟也一致。

降幅口径：以原耗时120分钟为分母，中文为(120−60)/120=50%；错误英文为(120−30)/120=75%。这些是审校核算，不是新增试点结论。

## 可直接替换的修订稿

中文：试点20单，耗时从120分钟降到60分钟。保留12个月。支持CSV，不支持自动下单。

English: Pilot of 20 orders, time decreased from 120 to 60 minutes. Retain data for 12 months. Supports CSV. Does not support automatic ordering.

术语：报价比较 = quote comparison；后文统一使用 quote comparison。

## 来源与边界

来源：samples/shared/bilingual.md（单段双语材料）。输入仅注明后文误用quotation contrast，没有提供独立的后文正文，因此修订稿已统一术语，但未声称修改其他未提供的文档。保留对象的具体范围需由文档负责人明确；本次未擅自补充产品规则。

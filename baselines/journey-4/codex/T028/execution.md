# 执行记录

工作目录：`<case-root>/T028`。

## 来源与实际读取

- 本 case 的 `AGENTS.md`、`TASK.md`、`samples/T028/task.md`。
- 唯一业务输入：`samples/shared/wide.csv`，2 个产品行，月份列为 2026-08、2026-09、2026-10。
- 公共执行规范：`<local-RTK-instructions>`，以及 Spreadsheets 的 `SKILL.md`。技能的通用工作簿输出规范按本任务明确指定的 CSV 交付要求处理；使用 Python 标准库进行 CSV 转换与 Decimal 精确求和，无 Excel 工作簿生成步骤。
- 未查询外网，未读取其他 case、历史、记忆或其他产品答案。

实际读取命令（退出码均为 0）：

```sh
rtk proxy cat AGENTS.md TASK.md
rtk proxy cat <local-RTK-instructions> <local-path> samples/T028/task.md samples/shared/wide.csv
```

第二条命令的长技能文档输出被工具截断，任务要求及完整 CSV 输入已显示。

## 实际转换命令与回执

以下命令已实际执行，退出码 0：

```sh
rtk proxy python3 -c 'import csv,json,hashlib; from pathlib import Path; from decimal import Decimal; p=Path("samples/shared/wide.csv"); rows=list(csv.DictReader(p.open(encoding="utf-8-sig",newline=""))); months=[k for k in rows[0] if k!="product"]; out=Path("results/T028"); out.mkdir(parents=True,exist_ok=True); detail=[{"product":r["product"],"month":m,"amount":r[m]} for r in rows for m in months]; target=out/"product_month_amount.csv"; f=target.open("w",encoding="utf-8",newline=""); w=csv.DictWriter(f,fieldnames=["product","month","amount"]); w.writeheader(); w.writerows(detail); f.close(); check=list(csv.DictReader(target.open(encoding="utf-8",newline=""))); source_sum=sum((Decimal(r[m]) for r in rows for m in months),Decimal(0)); output_sum=sum((Decimal(r["amount"]) for r in check),Decimal(0)); assert len(check)==len(rows)*len(months); assert source_sum==output_sum; assert check==detail; audit={"input_rows":len(rows),"month_columns":months,"expected_output_rows":len(rows)*len(months),"actual_output_rows":len(check),"source_total":str(source_sum),"output_total":str(output_sum),"difference":str(output_sum-source_sum),"by_product":{r["product"]:str(sum((Decimal(r[m]) for m in months),Decimal(0))) for r in rows},"by_month":{m:str(sum((Decimal(r[m]) for r in rows),Decimal(0))) for m in months},"output_columns":list(check[0]),"input_sha256":hashlib.sha256(p.read_bytes()).hexdigest(),"output_sha256":hashlib.sha256(target.read_bytes()).hexdigest()}; (out/"verification.json").write_text(json.dumps(audit,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(json.dumps(audit,ensure_ascii=False,indent=2)); print(target.read_text())'
```

真实输出关键回执：`input_rows=2`、`expected_output_rows=6`、`actual_output_rows=6`、`source_total=950`、`output_total=950`、`difference=0`。三个断言均执行完成，输出列为 product、month、amount。

CSV 内容：

```csv
product,month,amount
A,2026-08,100
A,2026-09,120
A,2026-10,150
B,2026-08,200
B,2026-09,200
B,2026-10,180
```

输入使用 `utf-8-sig` 读取以去除 BOM，输出使用 UTF-8。仅写入本 case 的 `results/T028`。输入未修改。输入和产物 SHA-256 记录在 `verification.json`。

保存后的独立分组复核也已执行，退出码 0：

```sh
rtk proxy python3 -c 'import csv,json; from pathlib import Path; from decimal import Decimal; from collections import defaultdict; p=Path("results/T028"); r=list(csv.DictReader((p/"product_month_amount.csv").open())); a=json.loads((p/"verification.json").read_text()); groups={}; [(groups.setdefault(k,defaultdict(Decimal))) for k in ("product","month")]; [(groups[k].__setitem__(v[k],groups[k][v[k]]+Decimal(v["amount"]))) for v in r for k in groups]; assert {k:str(v) for k,v in groups["product"].items()}==a["by_product"]; assert {k:str(v) for k,v in groups["month"].items()}==a["by_month"]; assert all((p/n).is_file() for n in ("result.md","execution.md","verification.json","product_month_amount.csv")); print("Saved files verified; output product/month subtotals match source controls; 6 records, total 950.")'
```

输出：`Saved files verified; output product/month subtotals match source controls; 6 records, total 950.`

## 计算口径和限制

源表全部金额单元格合计，与重读输出 CSV 的全部 amount 合计使用 Decimal 比较；以输入产品行数乘月份列数核对明细数，并逐条核对产品、月份、金额映射。产品和月份分组汇总见 result.md 与 verification.json。

原始数据没有缺失金额、重复产品或非数值金额；没有进行填零、删除、聚合或单位换算。币种和金额单位未提供，保留未知。无未完成项。

# 执行记录

## 来源与实际读取

仅使用本 case 的 `AGENTS.md`、`TASK.md`、`samples/T075/task.md` 和 `samples/shared/inventory.csv`，另读取授权要求的公共工具规则 `<local-RTK-instructions>`。未使用历史、记忆、其他 case 或其他产品答案。

实际执行的读取命令（均成功，退出码 0）：

```sh
rtk read <local-RTK-instructions>
rtk read <case-root>/T075/AGENTS.md
rtk read <case-root>/T075/TASK.md
rtk read samples/T075/task.md
rtk read samples/shared/inventory.csv
```

CSV 原始内容：

```csv
sku,on_hand,daily_demand,lead_days,safety_stock,on_order
A,50,10,7,20,0
B,200,5,10,30,50
```

## 实际计算与产物

在本 case 根目录执行 `rtk proxy python3 - <<'PY'` 内联脚本，以 `utf-8-sig` 读取 CSV、用 `csv.DictReader` 解析 2 行，使用 `Decimal` 按规定公式计算，实际写入 `results/T075/result.md` 和本执行记录。

执行输出：

```text
A: target=90, net=40, suggested=40
B: target=80, net=-170, suggested=0
```

逐项核对：A 为 10×7+20−50−0=40；B 为 5×10+30−200−50=−170，归零后为 0；建议补货总量为 40。输入共 2 行，SKU 为 A、B，无遗漏。

输入文件 SHA-256：`548cb538b8503da28c397fac8ad9c6711ad33ef02ec740459ec732ca44512afa`。

## 限制

计算依赖库存快照及稳定需求、及时到货等假设，详见 result.md；未获取实时库存或到货日期，未下单。任务要求的结果已实际生成，无未完成项。

## 保存后核对

`rtk read results/T075/result.md` 返回退出码 0，已回读确认 A=40、B=0、合计=40，公式与假设均已保存。计算脚本执行退出码为 0。

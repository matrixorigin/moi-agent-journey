# T030 执行记录

## 来源和边界

仅读取本case的`AGENTS.md`、`TASK.md`、`samples/T030/task.md`、`samples/shared/sales.csv`，以及引用的公共工具规则`<local-RTK-instructions>`。未访问其他case、历史、记忆、真实客户资料或外部服务；未委派。

## 实际命令与回执

所有shell命令以`rtk`开头，工作目录均为本case根目录。

1. `rtk proxy cat AGENTS.md TASK.md`：退出码0，确认只执行T030，输出正文及执行记录至`results/T030`。
2. `rtk proxy cat <local-RTK-instructions> samples/T030/task.md samples/shared/sales.csv`：退出码0，读取规则、任务及全部16行销售记录。
3. `rtk proxy python3 - <<'PY' ... PY`：实际运行Python标准库`pathlib`、`csv`、`io`、`json`计算。以`utf-8-sig`读取、`csv.DictReader`解析；检查文本前130字符、字面转义换行、状态计数、订单号重复、金额乘积，以及各月paid记录与渠道汇总。退出码0，回执如下。

```text
literal_crlf_count: 0（读取后为正常换行，未发生字面转义替换）
rows: 16; paid: 15; cancelled: 1
amount_mismatches: []; duplicate_order_ids: 0
2026-09: paid_orders=6, customers=6, qty=6, amount=900
  代理: orders=3, customers=3, amount=600
  官网: orders=3, customers=3, amount=300
2026-10: paid_orders=9, customers=6, qty=18, amount=2400
  代理: orders=3, customers=3, amount=1200
  官网: orders=6, customers=3, amount=1200
```

4. `rtk proxy python3 - <<'PY' ... PY`：实际创建交付目录，使用`Path.write_text(..., encoding='utf-8')`写入`result.md`及本记录。正文以一页篇幅组织三条发现、两项行动、一项待确认。

## 计算与核对

- 订单日期按月归属，先过滤`paid`再汇总，OX取消单100不计入10月2,400。没有重复订单号。
- 销售额增幅=(2400/900−1)=166.7%；订单增幅=50%；件数增幅=200%。
- 客单价区分每单与每客户：900/6=150；2400/9=266.67；2400/6=400。
- 官网增量900、代理增量600，总增量1,500，官网贡献900/1500=60%；份额300/900=33.3%，1200/2400=50%。
- 直接逐行核对：两月paid客户集合均为C01—C06；官网C01—C03，代理C04—C06；A仅官网且单价100，B仅代理且单价200。paid日期9月仅1日、10月仅1—2日，取消单为10月10日。

## 限制与未完成项

未获得完整期间覆盖、币种、退款、税费、成本或获客漏斗，不能确认完整月份趋势、财务收入、利润或渠道因果效果；完整性已列为待确认项。任务仅要求Markdown一页简报，未要求PDF或固定纸张排版，因此未生成PDF，实际分页取决于阅读器。要求的两份Markdown均已写入；无其他交付未完成。

## 写入后验证

实际运行`rtk proxy python3 - <<'PY' ... PY`读取两份UTF-8产物，确认文件存在且非空，并断言正文含三条发现、两项行动、一项待确认与统计口径；退出码0。

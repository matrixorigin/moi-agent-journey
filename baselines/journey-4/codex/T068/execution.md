# 执行记录

## 来源与范围

只读取本 case 的 AGENTS.md、TASK.md、samples/T068/task.md 和 samples/shared/scan.pdf；另读取用户指定的 <local-RTK-instructions> 及公共 PDF 工具说明。未读取其他 case、历史、记忆、产品答案或评分。所有产物写入 results/T068。

## 实际执行

1. `rtk read <local-RTK-instructions>`，退出码 0，确认 shell 必须以 rtk 开头。
2. `rtk read AGENTS.md TASK.md`，退出码 0，确认任务与访问范围。
3. `rtk read samples/T068/task.md <local-path>`，退出码 0。按 PDF 说明执行渲染和视觉读取。
4. 实际 shell 命令：

```sh
rtk proxy sh -c 'command -v pdftoppm; command -v pdfinfo; command -v python3; ls -l samples/shared/scan.pdf; mkdir -p results/T068; pdfinfo samples/shared/scan.pdf; pdftoppm -r 150 -png samples/shared/scan.pdf results/T068/scan'
```

退出码 0。pdfinfo 确认文件共 1 页，A4，未加密，36578 字节；Poppler 路径 /opt/homebrew/bin/pdftoppm。生成 scan-1.png。
5. 使用 view_image 实际打开 scan-1.png，检查整页。视觉读取 Invoice: QL-001；Date: 2026-10-20；Item: Sensor A；Quantity: 10；Unit price: CNY 120；Tax: CNY 156；Total: CNY 1356；Shipping: not stated。没有使用其他文本替代扫描图像。
6. `rtk proxy python3 - <<'PY' ... PY` 实际执行 Python 标准库 csv、decimal、json 脚本，写入 invoice.csv、result.md、verification.json；退出码 0。脚本将上述视觉读取值写入 CSV，使用 csv.DictReader 回读，并断言记录数为 1、全部 14 列内容与写入值一致，使用 Decimal 核对数量乘单价加税额等于总额。实际输出：

```json
{"csv_rows": 1, "columns": 14, "subtotal": "1200.00", "tax": "156.00", "total": "1356.00", "subtotal_plus_tax_equals_total": true, "shipping_is_blank": true, "shipping_status": "not_stated", "uncertain_fields": "shipping"}
```

7. `rtk proxy python3 - <<'PY' ... PY` 写入本执行记录，并核对产物存在且非空。

## 口径与限制

CSV 一张发票一行；金额统一两位小数，币种独立为 CNY；日期保留原文 ISO 格式。运费原文明确未注明，因此 shipping 留空、shipping_status=not_stated、uncertain_fields=shipping。未注明不代表免运费；数量、单价和税额的算术一致性不足以推断运费。未发现其他字形不确定项。此任务未创建或编辑 PDF，仅渲染原文件供读取。没有未完成的文件交付；运费真实金额需要额外来源才能确定。

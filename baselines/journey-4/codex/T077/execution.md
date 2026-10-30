# 执行记录

## 来源

- 本 case 的 `AGENTS.md`、`TASK.md`。
- `samples/T077/task.md`：按 SKU、规格识别采购合并并输出数量、最早交付日及不能合并原因。
- `samples/shared/purchase_requests.csv`：读取全部 4 条申请；使用 UTF-8 BOM 兼容编码解析。
- 公共执行规范 `<local-RTK-instructions>`。

## 实际执行

1. `rtk proxy cat <local-RTK-instructions>`：读取 shell 执行规范。
2. `rtk proxy cat AGENTS.md TASK.md`：确认任务范围和交付位置。
3. `rtk proxy cat samples/T077/task.md samples/shared/purchase_requests.csv`：读取任务及原始输入。
4. `rtk proxy python3 - <<'PY' ... PY`：实际运行 Python 标准库 csv，以 `(sku, spec)` 分组，qty 转整数求和，ISO 日期取最小值，写入本目录正文与执行记录。实际输出：

```text
A M8 R1,R2 100 2026-11-10
A M10 R3 30 2026-11-10
B 12V R4 20 2026-11-08
```

数据核对：输入数量 40 + 60 + 30 + 20 = 150；输出分组数量 100 + 30 + 20 = 150，4 条申请全部覆盖。不同规格或不同 SKU 不跨组合并；同组日期不同采用最早需求期限。

## 限制及未完成事项

任务所需文本分析已完成。输入没有供应商交期、提前收货限制、计量单位和采购政策，无法确认供应商实际交付时间或更多商业合并限制。未下单、未发送邮件、未公开发布，也未读取其他 case 或真实客户资料。

5. 再次执行 `rtk proxy python3` 核对两个已保存文件均存在且非空，并检查正文包含三个采购分组；输出如下：

```text
result.md: exists, non-empty
execution.md: exists, non-empty
```

6. `rtk proxy python3` 对已保存正文的三行分组执行断言核对，实际输出 `Three saved procurement groups verified.`。

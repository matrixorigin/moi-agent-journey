# 执行记录

## 读取来源

仅读取本 case 的 `AGENTS.md`、`TASK.md`、`samples/T025/task.md`、`samples/shared/usage.csv`，以及明确引用的公共执行规则 `<local-RTK-instructions>`。未读取其他 case、历史或记忆；未委派，未访问外部客户数据。

## 实际命令与回执

所有命令工作目录均为本 case 根目录，shell 命令均以 rtk 开头。

1. `rtk proxy cat AGENTS.md TASK.md`：退出码 0，确认任务、读取范围与输出位置。
2. `rtk proxy cat <local-RTK-instructions> samples/T025/task.md samples/shared/usage.csv`：退出码 0，读取任务全文及原始 CSV。
3. `rtk proxy python3 - <<'PY' … PY`：退出码 0。实际使用标准库 csv、json、pathlib，按 utf-8-sig 读取 CSV，按 customer 与 month 分组；运行数值计算并写入 `results/T025/result.md`。执行代码核心为：

```python
rows = list(csv.DictReader(p.open(encoding='utf-8-sig')))
change_pct = (current / previous - 1) * 100
errors_per_run_pct = errors / runs * 100 if runs else None
```

真实输出核对：

| 客户 | 活跃天数环比 | 运行量环比 | 9 月错误数/运行次数 | 10 月错误数/运行次数 |
|---|---:|---:|---:|---:|
| C01 | 10.00% | 30.00% | 2.00% | 0.77% |
| C02 | −88.89% | −93.75% | 3.75% | 80.00% |
| C03 | −100.00% | −100.00% | 0.00% | null（无运行） |

程序回执：`rows= 6 customers= 3 months= ['10', '9']`；`written: results/T025/result.md`。

4. 本次 `rtk proxy python3 - <<'PY' … PY` 写入 execution.md，并读回两份产物，检查文件存在、非空、UTF-8 可读及关键结论。输出文件名、字节数与验证状态；具体回执见下节。

## 限制与未完成项

已完成输入数据的实际读取、计算和正文落盘。数据没有年份、月份完整性、客户规模、合同/付费状态、错误定义或业务成果，因此未做实际流失确认、原因归因或概率预测。没有错误日志，无法核实 C02 的具体故障。未执行客户联系；后续核查是建议，不是已经完成的业务动作。

## 产物验证回执

- `result.md`：存在，UTF-8 读回成功，2959 字节（记录追加前）。
- `execution.md`：存在，UTF-8 读回成功，2098 字节（记录追加前）。
- 关键结论检查通过：三客户均覆盖，C02 运行降幅、C03 比值不可计算、月份完整性限制已写入。

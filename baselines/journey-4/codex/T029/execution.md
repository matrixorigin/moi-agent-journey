# T029 执行记录

## 范围与来源

工作目录：`<case-root>/T029`。

读取本 case 的 `AGENTS.md`、`TASK.md`、`samples/T029/task.md` 和唯一业务数据 `samples/shared/demand.csv`。未读取其他 case、父目录答案、评测结果、会话历史或记忆，未委派，未调用真实客户资料或执行对外发送。

同时读取任务指定的公共执行约束 `<local-RTK-instructions>`，并参考公共工具文档 `<local-path>` 的数据分析与核对规范。本次交付为 Markdown 需求预测报告和 JSON 计算明细，没有创建或修改工作簿。未进行网络查询，所有业务结论均基于本地 CSV。

## 实际命令及回执

以下 shell 命令均以 `rtk` 开头。除公共文档外，文件操作均限于本 case；写入仅在 `results/T029`。

1. `rtk read <local-RTK-instructions>`：退出码 0，确认 shell 前缀约束。
2. `rtk read AGENTS.md`：退出码 0，确认单任务、读取边界及禁止委派。
3. `rtk read TASK.md`：退出码 0，确认未来三个月预测及交付路径。
4. `rtk read samples/T029/task.md`：退出码 0，确认输入与方法、假设、不确定性要求。
5. `rtk read samples/shared/demand.csv`：退出码 0，实际读到 `month,qty` 与 12 行数据，首行存在 UTF-8 BOM，文件使用 CRLF 换行。
6. `rtk read <local-path>`：退出码 0，工具输出部分截断；随后执行 `rtk proxy sed -n '120,300p' <local-path>` 补读中段，退出码 0。
7. 调用 `mcp__codex_app__load_workspace_dependencies({})`：成功，返回运行时版本 `26.1017.22136` 和 bundled Node 路径。
8. 通过 `apply_patch` 实际写入 `results/T029/forecast.mjs`，随后运行：

   ```text
   rtk proxy <runtime-dependencies>/node/bin/node results/T029/forecast.mjs
   ```

   退出码 0。关键输出：

   ```json
   {
     "records": 12,
     "historical_total": 1860,
     "model": {"a": 90, "b": 10, "t_definition": "2025-11 = 1", "sse": 0, "residual_standard_error": 0},
     "validation": {"method": "expanding origin, train 6..9 months, horizon 1..3", "predictions": 12, "linear_mae": 0, "naive_mae": 20},
     "forecast": [
       {"month": "2026-11", "base": 220, "low_scenario": 215, "high_scenario": 225},
       {"month": "2026-12", "base": 230, "low_scenario": 220, "high_scenario": 240},
       {"month": "2027-01", "base": 240, "low_scenario": 225, "high_scenario": 255}
     ],
     "totals": {"base": 690, "low_scenario": 660, "high_scenario": 720},
     "sha256": "8f4e1f6bd904d4988d795e36ebf3b6bdea4a7fc0d6e93d43dddcb15089fe060c",
     "saved": "results/T029/calculation.json"
   }
   ```

9. 通过 `apply_patch` 实际写入 `result.md` 与本执行记录。随后核对文件存在性、正文和 JSON 数值一致性，详见文末回执。

## 计算及数据核对

脚本移除 BOM 后按 CRLF/LF 解析，不改变源文件。实际断言校验了表头、12 条记录、月份唯一性、月份连续性、数值有限且非负，以及基准预测为 `[220,230,240]`。计算明细写入后重新解析 JSON 并断言预测明细一致。原始文件 SHA-256 如上，可用于复核输入版本。

回归的 t=1 对应 2025-11；每次回测仅拟合各起点之前的数据。每个回测的训练截止月、步长、实际量、趋势预测和朴素预测均实际写入 `calculation.json`。情景公式为 `210 + h×5` 与 `210 + h×15`，h=1、2、3；这是敏感性假设，不是从数据估计的概率区间。

## 限制与未完成项

所需预测报告、方法、假设、不确定性说明及执行计算均已生成。没有因工具故障搁置的交付项。输入未说明数量单位、2026-10 是否整月结算、促销、缺货及外生变量，未补造这些信息。只有一个年度且残差为零，无法从现有资料可靠校准未来概率区间或识别年度季节性；报告已明确此限制，并提供透明的非概率情景。

## 最终文件核对回执

实际运行以下命令，退出码 0：

```text
rtk proxy <runtime-dependencies>/node/bin/node --input-type=module -e 'import fs from "node:fs"; import assert from "node:assert/strict"; const p="results/T029/"; for (const f of ["result.md","execution.md","forecast.mjs","calculation.json"]) assert.ok(fs.statSync(p+f).size>0); const d=JSON.parse(fs.readFileSync(p+"calculation.json","utf8")); assert.equal(d.totals.base,690); assert.equal(d.validation.predictions,12); const report=fs.readFileSync(p+"result.md","utf8"); for(const r of d.forecast) for(const v of [r.month,r.base,r.low_scenario,r.high_scenario]) assert.ok(report.includes(String(v))); console.log("Verified: 4 nonempty files; report matches all 3 forecast rows; base total 690; 12 backtest predictions.");'
```

实际输出：`Verified: 4 nonempty files; report matches all 3 forecast rows; base total 690; 12 backtest predictions.`

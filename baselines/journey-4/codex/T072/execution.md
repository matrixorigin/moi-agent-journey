# 实际执行记录

## 来源与边界

仅读取本 case 的 `AGENTS.md`、`TASK.md`、`samples/T072/task.md`、`samples/shared/expenses.csv`，以及指定的公共 RTK 文档和 Spreadsheets 技能工具说明。未读取其他 case、评测结果、历史或记忆，未委派、未发送消息或公开发布。所有写入位于本 case 的 `results/T072`。

输入 SHA-256：`b712eadfedd9e082d9eee72291dab21db31de0c8c60f7e13334a832db669777a`。

## 命令与回执

1. 执行 `rtk read <local-RTK-instructions>`，读取 shell 前缀要求。执行 `rtk read` 读取本 case 的 `AGENTS.md` 和 `TASK.md`，退出码均为 0。
2. 在本 case 根目录执行 `rtk read samples/T072/task.md`、`rtk read samples/shared/expenses.csv`，退出码均为 0。输入表头为 `id,department,project,type,amount`，原始明细为 E1=1000、E2=1200、E3=1000、E4=50000、E5=800。
3. 使用 `rtk read` 及 `rtk proxy cat` 读取公共 Spreadsheets 技能说明；调用 `load_workspace_dependencies` 获得 bundled Python 路径。任务明确要求 CSV，因此使用标准库直接输出 CSV，没有创建 XLSX、公式工作簿或渲染预览。
4. 通过 `apply_patch` 将实际分析脚本写入 `results/T072/analyze.py`。在本 case 根目录执行：

   ```text
   rtk proxy <runtime-dependencies>/python/bin/python3 results/T072/analyze.py
   ```

   退出码 0，生成 5 个 CSV 和 `verification.json`。标准输出回执：输入 5 笔、唯一 ID 5 个、金额合计 54000；Q1=1000、median=1000、Q3=1200、异常上界 1500.0；异常 ID 为 E4。
5. 脚本生成后用 `csv.DictReader` 重新读取每个汇总 CSV，断言每个维度金额合计为 54000、笔数合计为 5；重新读取异常 CSV，确认其 ID 集合与计算的超限记录一致。断言均实际执行，无报错。
6. 执行以下命令回读全部已保存 CSV：

   ```text
   rtk proxy python3 -c 'from pathlib import Path; p=Path("results/T072"); files=sorted(p.glob("*.csv")); [(print(f.name),print(f.read_text(encoding="utf-8-sig"))) for f in files]'
   ```

   退出码 0。读取结果为部门 2 组，项目 3 组，类型 3 组，联合 3 组；异常 1 笔 E4。检查各 CSV 的列名、金额、笔数、原始 ID、异常证据文字，无缺失。

## 计算口径与限制

所有原始记录均纳入，未删除异常。金额以 Decimal 计算，不使用二进制浮点求和；金额两位小数、百分比四位小数仅在输出时格式化。分位数采用 `(n−1)×p` 位置线性插值，筛选条件严格大于 Q3+1.5×IQR。部门、项目、类型分别汇总，并附三维联合汇总，以消除“按三个维度汇总”的歧义。每组保留 source_ids，异常表保留 peer_ids，便于回溯。

独立算式核对：研发=1000+1200+800=3000；市场=1000+50000=51000；总计=3000+51000=54000。A/云资源=2200，B/活动=51000，C/差旅=800。E4/整体中位数=50000/1000=50，E4/同组其余笔均值=50000/1000=50，占总额=50000/54000×100=92.5926%。

缺少币种、日期、预算、审批、发票和合同等数据，因此无法确认异常原因、跨期趋势或是否超预算。未开展外部查证，未将统计异常判定为业务错误。任务要求的汇总和异常 CSV 均已实际生成，无工具阻塞或未完成文件。

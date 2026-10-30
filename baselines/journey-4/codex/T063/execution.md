# T063 执行记录

## 来源与边界

只读取本case `AGENTS.md`、`TASK.md`、`samples/T063/task.md`、指定输入 `samples/shared/report_50pages.pdf`，以及必要公共工具说明（RTK.md、PDF技能文档、运行时依赖路径）。未读取其他case、历史、记忆、评分或其他产品答案；没有委派、发信、下单或发布。所有产物位于 `results/T063`。

## 实际命令与回执

所有shell命令均以 `rtk` 开头。核心命令如下（工作目录均为本case）：

1. `rtk read AGENTS.md`、`rtk read TASK.md`、`rtk read samples/T063/task.md`：成功读取任务边界。
2. `rtk read <local-RTK-instructions>` 及 `rtk read <local-path>`：读取必要工具说明。
3. `rtk proxy python3 -c ...`：探测默认Python，pypdf/reportlab/pdfplumber均不可用；系统Poppler三个命令可用。调用依赖工具取得bundled Python；bundled `import reportlab,pypdf` 成功。没有安装依赖。
4. `rtk proxy python3 -c ...` 调用 `pdfinfo samples/shared/report_50pages.pdf` 与 `pdftotext -layout samples/shared/report_50pages.pdf -`：实际获取50页全文并按form-feed逐页读取。原文为A4、50页、46,162字节、未加密。
5. 在PDF技能公共目录定位marker后，执行 `rtk proxy <runtime-dependencies>/node/bin/node <local-path> --operation-kind create --expected-output-count 1 --output-format pdf`：exit 0；仅执行一次。
6. `rtk proxy python3 - <<'PY' ... PY`：在results目录写入完整可复现生成脚本 `build_summary.py`。
7. `rtk proxy <runtime-dependencies>/python/bin/python3 results/T063/build_summary.py`：exit 0。脚本实际提取50页、检查逐页订单/金额与重复声明、生成两页PDF并重开核验页数。控制台回执为source_pages=50、summary_pages=2、repeated_core_metric_pages=50、no_new_permission_issue_pages=49、exception_pages=[37]、derived_minutes_reduction=60、derived_reduction_percent=50。SHA-256与逐章行保存于 `verification.json`。
8. `rtk proxy python3 - <<'PY' ... PY`：以 `pdftoppm -f N -singlefile -scale-to 1200 -png` 渲染原文N=1、37、50页；以 `pdftoppm -scale-to 1400 -png results/T063/procurement_summary_2pages.pdf results/T063/summary` 渲染摘要两页；均exit 0。随后通过view_image实际查看全部5张图，核对中文、引用和版面；摘要两页均无截断、重叠或缺字。
9. `rtk proxy python3 - <<'PY' ... PY`：重新调用pdftotext，逐项断言摘要包含20、120、60、50%、37、跨部门权限泄露、无对照组、不能推断因果、合成样例、3,700、5,000。回执“Summary key-number and limitation text checks completed.”；pdfinfo确认2页，输出保存 `pdfinfo.txt`，提取文本保存 `summary_extracted.txt`。

## 计算与覆盖口径

- 完整读取页数：50，章节P01-P50；逐页章号、订单字段和金额模式全部核对。原文提取留存 `source_extracted.txt`。
- 下降分钟数=120-60=60；相对降幅=(120-60)/120=50%。仅为描述性计算，没有统计显著性或因果推断。
- “20单”是各页重复的总体口径，未按50页累加。
- 第i章写“本章订单i”、金额100×i元；字段含义与总体样本对应关系未解释，未计算试点总订单数、采购总额或收益。
- 49页写“未发现新增权限问题”，第37页明确权限泄露且必须先修复；保留该例外，未声称已修复。
- 所有页均声明数字为合成样例，不能用来作真实业务结论。
- 页码引用采用PDF物理页码，与原页脚Page及章节序号一一对应。

## 限制与未完成项

要求的两页摘要PDF、result.md、execution.md均已实际生成。原文没有逐单耗时、对照组、统计分布、采集时间、权限泄露范围/根因或修复复测证据；摘要明确保留这些证据限制，没有补造。原报告50页全部作文本读取，原文视觉抽查第1、37、50页；成品两页全部渲染并目视检查。

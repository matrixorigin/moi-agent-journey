# T100 执行记录

## 来源与读取范围

工作目录为本case根目录。读取 `AGENTS.md`、`TASK.md`、`samples/T100/task.md`、`samples/shared/customer.md`、`samples/shared/product.md`、`samples/shared/quotes.csv`。`rtk ls samples`仅列出本case样例包目录名称，没有读取其他任务内容。未读取记忆、其他case或历史答案，未委派。

公共工具文档：`<local-RTK-instructions>`，documents与spreadsheets的SKILL.md、文档参考发现说明、表格API_QUICK_START、create_workflows和style_guidelines。参考发现不访问真实客户私有资料，采用新建朴素提案设计。

在线检索实际使用web工具，先检索私有部署文档与存储留存，随后打开并采用以下一手资料，查阅日期2026-10-29：

- https://docs.ollama.com/faq ：本地运行、禁用云功能 `OLLAMA_NO_CLOUD=1`，仅支持候选本地模型路线判断。
- https://www.postgresql.org/docs/current/backup.html ：定期备份及SQL dump、文件系统备份、连续归档，支持候选备份路线判断。
- 初始搜索返回ONLYOFFICE Docker和MinIO object retention资料，未将其作为最终产品能力或费用依据。

## 实际命令与回执

以下命令均以rtk开头，路径缩写仅用于本记录阅读：PY为依赖加载器返回的 `<runtime-dependencies>/python/bin/python3`，NODE为同目录 `node/bin/node`，DOC为 `<local-path>`，SHEET为同运行时版本下的spreadsheets/skills/spreadsheets。

1. `rtk read`依次读取上述约束、三个源文件和任务文件。三家各100件，单价100/98/105，税率13%，运费500/空/0，交期7/5/3天，质保12/12/24个月。
2. 调用 `load_workspace_dependencies` 取得打包Python、Node和依赖路径。`rtk proxy mkdir -p results/T100/qa` 和 `rtk proxy ln -s <bundled node_modules> results/T100/node_modules` 完成隔离构建准备。
3. 使用 `rtk proxy NODE <DOC或SHEET>/container_tools/mark_artifact_operation_started.mjs --operation-kind create --expected-output-count 1 --output-format docx或xlsx` 各执行一次，exit 0。
4. 通过apply_patch创建本目录的 `build_doc.py` 与 `build_sheet.mjs`。DOCX采用打包python-docx，XLSX采用打包 `@oai/artifact-tool`。`rtk proxy PY results/T100/build_doc.py` 返回DOCX saved，exit 0。
5. `rtk proxy NODE results/T100/build_sheet.mjs` 首次暴露浮点数11799.999999999998，断言停止，没有把失败当作完成。金额公式加入ROUND到2位小数后重跑，返回 `XLSX saved; totals 11800 / pending / 11865; 726 freight tie and included-tax 10500 / threshold 700 tested; source rows 3`。随后新增明确0运费测试，发现表格引擎将0与空字符串比较时等同，报 `Explicit zero freight error`。把缺项判断改为ISNUMBER后重新生成并完成断言，明确输入0运费计算11074，未知空值与0区分。最后恢复未税口径和S2运费空白再导出，exit 0。
6. 初次探测 `DOC/scripts/render_docx.py` 不存在，随后通过 `rtk proxy find DOC -name '*render*'` 定位实际 `DOC/render_docx.py`。运行 `rtk proxy PY DOC/render_docx.py results/T100/青岚制造试点提案.docx --output_dir results/T100/qa/docx --emit_pdf`，返回Pages rendered。图像检查发现中文缺字，未据此交付。
7. `rtk proxy fc-list :lang=zh family file` 确认系统中文字体。将文档字体统一为Arial Unicode MS并移除Title样式默认边框；仅改字体仍缺字。读取公共soffice wrapper确认打包渲染器后，在本目录生成 `qa/fonts.conf` 指向系统字体目录，使用 `rtk proxy env FONTCONFIG_FILE=<本结果目录>/qa/fonts.conf SAL_FONTPATH=/System/Library/Fonts PY DOC/render_docx.py results/T100/青岚制造试点提案.docx --output_dir results/T100/qa/docx --emit_pdf`。exit 0，最终5页中文正常。
8. 用view_image打开最终page-1至page-5和报价表预览，检查文字、表格、页脚和来源。最终中文可读，未见溢出或遮挡。page-5为资料依据页。PNG/PDF仅为本地验证材料，不替代DOCX交付。
9. 实际执行 `rtk proxy PY -c <ZipFile + Document + openpyxl只读校验代码>`，重新读取两个最终文件并保存 `qa/verification.json`。DOCX可读取35个段落与5张表。两个ZipFile.testzip()均返回None。XLSX有2张表、2项输入校验；原始报价E3保持空值；关键公式实际存在且缓存值为H9=11800，H10=待确认，H11=11865，B16=726，B17=791。openpyxl仅用于读取校验，未用于生成工作簿。

## 计算口径与文件验证

主口径明确为待确认假设：人民币、未税单价、货款税率13%、运费最终含税。货款含税=ROUND(数量×单价×1.13,2)，总额=货款含税+已确认运费。缺运费时输出文字待确认，不计算最低总价或授标。含税口径不重复加税。三家数量和税率来自原始CSV，日期及验收指标为建议。5万元属于软件试点预算，未与货物报价混为软件费用。

核对：100×100×1.13+500=11800；100×98×1.13=11074；100×105×1.13=11865；11800−11074=726；11865−11074=791。含税单价时100×100+500=10500，100×105=10500，临界运费=10500−9800=700。

`qa/workbook-inspection.json`和XLSX export inspect文件保留工作簿检查证据；`qa/报价比较.png`及`qa/原始报价.png`为表格渲染；`qa/docx/`为最终渲染页面和内部PDF。生成脚本保留便于复现。

## 限制与未完成的业务动作

没有真实产品部署包或客户服务器，因此未执行私有部署、断网验证、恢复演练或20单客户试点。提案将这些列作验收任务而非已完成结果。没有正式软件及硬件报价，不宣称5万元足够。没有币种、商品规格、运费税务口径、付款条件和有效期，不能最终选定货物供应商。所有公开研究仅证明候选路线，不能补足虚构产品未披露能力。未发送邮件、公开发布、下单或访问真实客户私有资料。

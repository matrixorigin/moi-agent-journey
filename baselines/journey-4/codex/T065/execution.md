# T065 执行记录

## 来源与边界

仅使用本 case 的 AGENTS.md、TASK.md、samples/T065/task.md 和 samples/shared/notes.md；公共工具文档为 <local-RTK-instructions> 及 documents 技能和其引用文档。未读取其他 case、父目录答案、历史、记忆或真实客户资料。未委派。

原始业务笔记全文：
> 新员工笔记：第1天拿公司账号，找IT开权限。资料在部门知识库。报价要先核规格，再核税运费。超过5万找经理和财务。客户资料不能进公开群。出错找值班，保留订单号和时间，不贴密码。周五复盘。

## 处理口径

保留上述业务规则；金额为严格超过50,000，未改成大于等于，也未擅定币种、含税含运费口径。账号、知识库、经理、财务、值班人员和复盘入口均无真实联系方式，列为待确认。新增的异常模板、复盘准备、自查项目均作为培训建议，不冒充公司制度。未搜索外部旧手册，因为任务限定仅可读取当前样例和公共工具材料。

## 实际执行

以下命令在当前 case 根目录执行；所有 shell 命令均以 rtk 开头。

1. `rtk read AGENTS.md`、`rtk read TASK.md`、`rtk read samples/T065/task.md`、`rtk read samples/shared/notes.md`：退出码均为0，读取要求和原始笔记。
2. `rtk read <local-RTK-instructions>` 与 documents 技能说明及 reference discovery：退出码0。
3. 调用 load_workspace_dependencies，返回 bundled Python `<runtime-dependencies>/python/bin/python3` 与 Node 路径。
4. `rtk proxy <runtime-dependencies>/node/bin/node <local-path> --operation-kind create --expected-output-count 1 --output-format docx`：退出码0。
5. `rtk proxy mkdir -p results/T065`：退出码0。通过 rtk proxy bundled Python heredoc 写入 build.py，再运行 `rtk proxy <runtime-dependencies>/python/bin/python3 results/T065/build.py`，最终回执 `Created DOCX 39450`。
6. 运行 packaged render_docx.py，输出3页 PNG 和内部 QA PDF。初次字体 PingFang SC 缺字；改用 Arial Unicode MS 仍未加载中文。检查字体列表、renderer 及 bundled soffice wrapper 后，提供本 case 下 fonts.conf 与 SAL_FONTPATH 重新渲染。
7. 最终渲染命令：

```sh
rtk proxy env FONTCONFIG_FILE=<case-root>/T065/results/T065/fonts.conf SAL_FONTPATH=/System/Library/Fonts/Supplemental <runtime-dependencies>/python/bin/python3 <local-path> results/T065/新员工培训手册.docx --output_dir results/T065/qa --emit_pdf
```

退出码0；回执 `Pages rendered to .../results/T065/qa`。实际调用 view_image 查看最终 page-1.png、page-2.png、page-3.png：中文可读，标题和表格完整，无可见裁切、重叠；3页分别覆盖入职与报价、安全与异常及复盘、待确认清单与自查。QA 文件只作检查留档，不作为额外正文交付。

8. bundled Python 用 python-docx 重开最终 DOCX 并检查 OOXML：39450字节、46个段落、7个一级标题、1个表格；第1天、IT、部门知识库、超过5万、经理和财务、公开群、订单号、发生时间、密码、周五、待确认全部存在；settings.xml 不含 documentProtection。核对回执存 verification.txt。实际生成 result.md 和本 execution.md。

## 限制与未完成信息

联系方式、链接、金额币种及口径、审批确认方式、值班升级方式和周五具体安排无法从笔记确认，已在手册保留待确认；未编造姓名、号码或链接。没有实际办理入职、开权限或联系人员，也没有执行真实报价。DOCX 已完成生成、结构检查和逐页渲染检查；编辑能力依据原生 OOXML 及无编辑保护确认，未在 Word 图形界面进行人工编辑。

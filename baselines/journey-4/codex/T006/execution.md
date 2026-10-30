# T006 执行记录

## 读取来源

- 本 case `AGENTS.md`：仅执行本任务、命令以 rtk 开头、不得读取其他 case 或历史、不得委派。
- `<local-RTK-instructions>`：shell 使用 rtk，原生命令通过 `rtk proxy` 执行。
- 本 case `TASK.md` 与 `samples/T006/task.md`：要求设计首次使用、成功、失败恢复和退出的完整用户旅程。
- `samples/shared/feature.md`：采购报价比较；CSV/Excel 上传；识别供应商、规格、数量、税费、运费、交期；对照表；经理确认后导出；缺失提示；采购编辑、财务看价格、访客仅看公开摘要。

## 实际执行

以下命令均在本 case 根目录执行；公共 RTK 文档以绝对路径读取。

1. `rtk read AGENTS.md`：退出码 0，读取 case 执行约束。
2. `rtk read TASK.md`：退出码 0，确认任务 T006 与输出路径。
3. `rtk read <local-RTK-instructions>`：退出码 0，确认 rtk proxy 用法。
4. `rtk read samples/T006/task.md`：退出码 0，确认四条旅程要求。
5. `rtk read samples/shared/feature.md`：退出码 0，取得完整功能输入。
6. `rtk proxy python3 - <<'PY' ... PY`：实际创建 `results/T006/result.md` 与本执行记录；采用 pathlib 写入 UTF-8 正文。
7. `rtk proxy python3 - <<'PY' ... PY`：读取两个输出文件，核对存在、非空、UTF-8 可读及正文包含四条旅程和七类识别字段；回执见下方。

## 核对口径与限制

本任务为需求设计，不涉及业务数据计算。七类字段逐项核对，角色授权分别列出。必需字段拦截、版本、草稿、映射纠错等均明确为设计建议，未伪称现有功能。未读其他附属数据，因为给定功能文件已覆盖本任务输入。

没有产品原型、实现或可登录测试环境；未执行实际上传、审批、权限或导出测试，验收走查是后续实施验证要求。已完成文本交付，没有另需生成的 PDF、图像或代码产物。未访问真实客户资料，未发信、发布、下单或委派。

## 文件核对回执

核对完成：result.md 与 execution.md 均存在、非空、UTF-8 可读；四条旅程、七类字段与权限角色均已覆盖。result.md 字符数：3389。命令退出码 0。

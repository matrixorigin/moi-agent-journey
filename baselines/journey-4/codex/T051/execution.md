# T051 执行记录

## 来源及边界

仅读取本 case 的 AGENTS.md、TASK.md、samples/T051/task.md、samples/shared/customer.md、samples/shared/product.md，以及用户要求的公共 shell 指令 <local-RTK-instructions>。未读取父目录答案、其他 case、历史或记忆；未委派；未连接真实客户系统、发送消息或公开发布。

客户材料提供公司/团队规模、工作量、部署/中文/审批/留存要求、角色、预算、决策窗口和无 ERP 接口事实。产品材料提供五项支持能力、三项不支持能力和 20 单试点均值。脚本中三家供应商、物料、价格、税率、CSV 字段和试点设计均为本次合成准备或建议，不是来源中的真实业务数据或产品配置证明。

## 实际命令与回执

所有 shell 调用均以 rtk 开头，工作目录为本 case 根目录。

1. `rtk read <local-RTK-instructions>`、`rtk read <本case绝对路径>/AGENTS.md`、`rtk read <本case绝对路径>/TASK.md`：均退出 0，取得执行约束。
2. `rtk read samples/T051/task.md`、`rtk read samples/shared/customer.md`、`rtk read samples/shared/product.md`：均退出 0，完整读取需求和输入。
3. `rtk proxy python3 - <<'PY'`：通过 Python pathlib/csv 在 results/T051 写入 result.md、demo_quotes.csv；退出 0。实际回执为 `Created result.md and demo_quotes.csv`，正文 9296 bytes，CSV 456 bytes；时间段合计 30。第一次普通浮点计算出现 11399.999999999998 / 10921.999999999998 的二进制精度尾差，正文使用正确金额，随后用 Decimal 精确复核。
4. 再运行 `rtk proxy python3 - <<'PY'`：从已保存 CSV 回读三条记录，用 Decimal 按含税标记及含税运费重新计算并断言；核对正文最后时间段存在；实际输出为“模拟供应商甲 11400.00 / 模拟供应商乙 10800.00 / 模拟供应商丙 10922.00”，以及 `Readback: 3 CSV records; exact totals match; timeline = 30 minutes; final segment present.`。本命令同时写入本执行记录。

## 核对口径

时间分配 3+3+6+5+3+5+3+2=30 分钟。甲丙未税单价乘 1.13，乙含税单价不重复加税，各加含税运费；乙分别低于丙 122 元、甲 600 元。客户现状 30 单/周×2小时=60小时/周。样本 120→60 分钟只对应提供的 20 单，未推断青岚必然节约 50%；30 小时/周差额仅作带假设的算术解释。

## 交付及限制

实际生成 result.md（30 分钟脚本、数据准备、角色问题、限制、试点建议）、demo_quotes.csv（合成数据）、execution.md（本记录）。本任务为脚本设计，没有产品登录地址、账号、导入模板、部署规格或报价；未实际上传、审批、导出产品报告、验证中文界面、私有部署或一年留存，也未执行客户会议。CSV 未做产品兼容性认证；脚本已把这些列为会前排练或后续补证条件。没有未完成的脚本文档交付；现场产品验证不在本次已执行范围内。

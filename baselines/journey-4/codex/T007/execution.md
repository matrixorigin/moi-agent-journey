# 执行记录

## 读取来源

- 本case `AGENTS.md`：操作范围、RTK及交付约束。
- `<local-RTK-instructions>`：公共命令工具说明；仅用于执行方式。
- 本case `TASK.md` 与 `samples/T007/task.md`：版本迁移任务及交付要求。
- 本case `samples/shared/versions.md`：唯一业务依据，全文为：
  > 旧版：一个账号只能一个工作区，会话只支持删除，报价不含运费。新版：多工作区，会话可归档，报价须单列税费与运费，缺失运费标记待确认。旧数据不得补零。

## 实际命令与回执

在本case根目录执行，所有shell命令均以rtk开头：

1. `rtk read AGENTS.md`：退出码0，读取范围约束。
2. `rtk read TASK.md`：退出码0，读取交付要求。
3. `rtk read <local-RTK-instructions>`：退出码0，读取RTK用法。
4. `rtk read samples/T007/task.md`：退出码0，确认只输入版本方案。
5. `rtk read samples/shared/versions.md`：退出码0，获取新旧方案原文。
6. `rtk proxy python3 - <<'PY' ... PY`：使用pathlib实际创建交付目录并写入result.md和execution.md；无业务数据变更。
7. `rtk proxy python3 - <<'PY' ... PY`：读取两个交付文件，核对文件存在、非空、UTF-8可读及关键约束，回执记录在本节末尾。

## 分析口径与限制

无数值输入，无金额计算。缺失运费按未知/待确认处理，禁止转换成0；税费历史口径未给出，不推定可自动拆分。工作区权限、归档恢复、总额计算等仅作为迁移建议或待确认项。没有附属数据库、运行系统或迁移脚本要求，因此未执行实际系统迁移，未验证线上行为，未发送用户通知。没有读取其他case、历史/记忆或他产品结果，没有委派或自评分。

验证回执：文件核对完成：两个交付文件均存在、非空、UTF-8可读；正文覆盖行为变化、迁移影响、用户通知及缺失运费不得补零约束。

# 执行记录

## 读取来源

- `<local-RTK-instructions>`：shell命令规范。
- 当前case的 `AGENTS.md`、`TASK.md`：范围、读取与交付限制。
- `samples/T014/task.md`：综合五份材料的具体交付要求。
- `samples/shared/articles.md`：唯一业务输入，A—E五份合成材料。

未读取其他case、历史、记忆、其他产品答案或评分；未委派。

## 实际命令与回执

工作目录：`<case-root>/T014`。

1. `rtk proxy cat <local-RTK-instructions>`：退出码0，读取到所有shell命令必须以rtk开头的规则。
2. `rtk proxy cat AGENTS.md TASK.md`：退出码0，确认仅执行T014并写入results/T014。
3. `rtk proxy cat samples/T014/task.md samples/shared/articles.md`：退出码0，读取五份材料及输入末尾的待核查文章。
4. `rtk proxy python3 - <<'PY' ... PY`：实际创建交付目录并以UTF-8写入result.md和execution.md。脚本正文为本次文件写入操作；后续回读核验另记。

## 数据核对与限制

已依据输入逐项核对A—E的时间、样本单位和报告结果。计算仅包括82−78=4个百分点、18/100=18%、8/100=8%，以及类别重叠未知时的并集范围max(18,8)=18至18+8=26条。未估算显著性或合并准确率。

原始题集、逐题数据、抽样和评分口径缺失，无法复核准确率、因果关系与总体推广结论；这是输入证据限制，已在正文标注。输入末尾的外部陈述不属于T014的五材料综合任务，未进行联网核查。任务范围内无未完成交付。

## 交付回读

5. `rtk proxy python3 - <<'PY' ... PY`：退出码0；实际以UTF-8回读两文件，核对正文包含来源概览、一致结论、分歧、证据不足及计算口径；均已写入且非空。写入命令退出码0，输出列出了两份交付文件。

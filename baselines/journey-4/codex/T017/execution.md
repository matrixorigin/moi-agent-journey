# 执行记录

## 来源与边界

仅读取本case的 AGENTS.md、TASK.md、samples/T017/task.md、samples/shared/reviews.csv，以及其要求的公共工具说明 <local-RTK-instructions>。未读取其他case、历史、记忆或其他产品答案；未委派、访问外部服务或发送消息。

## 实际命令与回执

以下命令均在本case根目录执行，均以rtk开头，退出码均为0：

1. `rtk read AGENTS.md`：确认只执行本任务、仅在本case副本操作等约束。
2. `rtk read TASK.md`：确认100条评论分析、产物与执行记录要求。
3. `rtk read <local-RTK-instructions>`：确认shell使用rtk前缀，原生命令可使用rtk proxy。
4. `rtk read samples/T017/task.md`：确认输入与交付目录。
5. `rtk read samples/shared/reviews.csv`：实际读取全部100条评论及rating、id字段。
6. `rtk proxy python3 - <<'PY' ... PY`：运行Python标准库csv、collections、json、pathlib、hashlib；按utf-8-sig解析CSV，统计记录数、唯一ID、空字段、文本频次、评分及均值，生成counts.json。实际输出：100条，100个唯一ID，0条必填字段缺失，3种文本；引用出处清楚60条，大文件上传容易失败20条，表格答案不准确20条；5分60条、2分40条；平均3.8分。详细输出与每主题全部ID已保存counts.json。
7. `rtk proxy python3 - <<'PY' ... PY`：实际写入result.md和本execution.md；读取counts.json并断言总数100、主题频次[20,20,60]、主题合计100、唯一ID100、空字段0、评分条数合计100、均值3.8；检查三个交付文件存在且非空，输出VERIFIED及文件字节数。

## 计算口径与数据核对

统计单位为CSV记录；不按相同文本去重。每条文本对应单一主题，态度依原文分类并与评分核对。总样本分母100，认可分母60，不满分母40，因此两类不满各占全样本20%、占不满50%。不将记录数当独立用户数。

输入SHA-256：c356f59f45d8f1d141c9f99950689a6ffbc98ff1ae0933e12d76077e48b2918c。

## 交付与限制

已生成result.md（结论、主题统计、代表原文、优先建议和边界）、counts.json（可核对统计及全部ID）、execution.md（本记录）。未完成项：无。受输入所限，未验证技术根因、实际业务损失或全体用户分布；建议中的改进方案与优先级属于分析判断，未声称已实施。

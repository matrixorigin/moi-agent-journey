# 来源与实际执行记录

工作目录：`<case-root>/T093`。

## 读取来源

- 本 case 的 `AGENTS.md`、`TASK.md`。
- 明确引用的公共命令规范 `<local-RTK-instructions>`。
- `samples/T093/task.md` 和 `samples/shared/local_rules.md`。
- `samples/T093_rename` 的全部 3 个文件名及正文。三个正文均为 `synthetic content`，没有可补充的年份或主题信息。

未读取其他 case、历史、记忆、其他产品答案或评分。

## 实际命令与回执

全部 shell 命令均以 `rtk` 开头，读取命令如下，均退出 0：

```text
rtk read AGENTS.md
rtk read TASK.md
rtk read <local-RTK-instructions>
rtk read samples/T093/task.md
rtk read samples/shared/local_rules.md
rtk ls samples/T093_rename
rtk proxy python3 -c 'from pathlib import Path; import json; p=Path("samples/T093_rename"); print(json.dumps([{ "name": f.name, "content": f.read_text()} for f in sorted(p.iterdir()) if f.is_file()], ensure_ascii=False, indent=2))'
```

通过文件补丁工具写入本交付目录的 `rename.py`，实际执行：

```text
rtk proxy python3 results/T093/rename.py
```

退出码为 0；实际回执已写入 `receipt.json`，输出 `renamed=1`、`pending_confirmation=2`、`file_count_preserved=true`、`all_content_hashes_preserved=true`。脚本执行了 Path.rename，重命名前检查目标不存在，按映射逐项核对执行前后 SHA-256，核对文件总数及最终文件名集合，并实际生成 `mapping.csv`。

三个源文件的 SHA-256 均为 `a04dc6c38580462e46df7968a7d91a006455989bb839a6c9c1cf5fb4c4551a47`，执行后逐项相同。相同正文不构成删除依据，3 个文件全部保留。

交付完成后额外执行了下列只读核对，退出码 0：

```text
rtk proxy python3 -c 'from pathlib import Path; import csv,json,hashlib; out=Path("results/T093"); rows=list(csv.DictReader((out/"mapping.csv").open(encoding="utf-8-sig"))); receipt=json.loads((out/"receipt.json").read_text()); source=Path("samples/T093_rename"); assert len(rows)==3; assert sorted(p.name for p in source.iterdir())==receipt["after"]; assert all(hashlib.sha256((source/r["final_name"]).read_bytes()).hexdigest()==r["sha256_before"]==r["sha256_after"] for r in rows); assert all((out/f).is_file() and (out/f).stat().st_size>0 for f in ["result.md","execution.md","mapping.csv","receipt.json","rename.py"]); print("Verified: 3 mapped files; 1 renamed; 2 pending; all content hashes preserved; 5 deliverables nonempty.")'
```

实际输出：`Verified: 3 mapped files; 1 renamed; 2 pending; all content hashes preserved; 5 deliverables nonempty.`

## 规则与限制

完整日期转换为 `YYYY-MM-DD`，项目字段按任务统一为 `项目A`，从源名提取主题“会议”，保留扩展名。缺少年份同样属于日期不完整，保留原名并在映射中标记待确认。没有虚构年份、主题或以文件时间戳替代业务日期。

仍待确认两项：`项目A_10月1日.txt` 的年份和主题；`未注明日期.txt` 的完整日期和主题。其余处理已实际执行。脚本用于本次单次执行，重复运行会因原始文件名集合变化而停止，避免重复变更。

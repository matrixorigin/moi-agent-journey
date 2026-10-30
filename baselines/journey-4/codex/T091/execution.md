# T091 执行记录

## 读取来源

- 本 case 的 `AGENTS.md`、`TASK.md`。
- 公共命令规范 `<local-RTK-instructions>`。
- `samples/T091/task.md` 与指定输入 `samples/shared/local_rules.md`。
- `samples/T091_downloads/` 内全部 3 个文本文件，均实际读取正文。
- 未读取其他 case 正文、其他产品答案、评分或历史记忆。

## 实际命令与回执

所有命令均在本 case 根目录执行，以 rtk 开头。

1. `rtk read AGENTS.md`、`rtk read TASK.md`、`rtk read <local-RTK-instructions>`、`rtk read samples/T091/task.md`、`rtk read samples/shared/local_rules.md`：退出码均为 0。确认仅操作合成目录、不得删除源文件、产物写 results/T091。
2. `rtk ls samples`：退出码 0；仅列出目录项。`rtk ls samples/T091`：退出码 0，仅 task.md。
3. `rtk proxy find samples/T091_downloads -type f -maxdepth 3 -print`：退出码 0，发现 invoice_20261001.txt、project_notes.txt、meeting_20261002.txt。
4. 分别运行 `rtk read samples/T091_downloads/invoice_20261001.txt`、`rtk read samples/T091_downloads/project_notes.txt`、`rtk read samples/T091_downloads/meeting_20261002.txt`：退出码均为 0；正文依次为 `invoice amount 100`、`project A notes`、`meeting about price`。
5. 第一次 `rtk proxy python3 - <<'PY'`：用 pathlib 创建交付目录，按上述正文形成三条分类规则，计算各源文件字节数与 SHA-256；先保存 move_preview.md 与 mapping.json，并输出完整预览。退出码 0；回执“执行前预览及映射已保存；源文件数：3”。本步骤未复制文件。
6. 第二次 `rtk proxy python3 - <<'PY'`：读取已保存映射，目标存在则报错以避免覆盖；使用 shutil.copy2 逐项复制至 organized，重新计算源/目标 SHA-256，断言两者与预览记录一致；核对源目录仍有 3 个文件、分类目标有 3 个 txt；写 verification.json。退出码 0，实际回执如下。

```json
{"source_count": 3, "classified_count": 3, "source_preserved": true, "all_hashes_match": true, "total_bytes": 52}
```

| 文件 | 字节 | 复制后 SHA-256 |
|---|---:|---|
| invoice_20261001.txt | 18 | 42794d61489557e84c9d8fba93282db351a3df7127cd3762a7120376fdff1801 |
| project_notes.txt | 15 | 81bc27c6f7f908c69fd3b2fc850eb93b6c901ae086f333389ea8f9be16cec709 |
| meeting_20261002.txt | 19 | a369784b50979efddad41531715142f6099c821ef3b024495156ec0dbaa49b5a |

7. 第三次 `rtk proxy python3 - <<'PY'`：用 pathlib.write_text 保存 result.md 与本 execution.md。

## 口径与限制

分类依据为文件名和实际正文，不仅按统一的 txt 扩展名分类。三个分类为财务票据、项目资料、会议记录。字节数取原始文件 bytes 长度，校验采用 SHA-256。

为同时遵守不得删除源文件和只写 results/T091，执行的是分类复制；没有把复制冒充为移动。未执行源目录原地整理，因此实际“移动”部分未完成。未删除、重命名或覆盖源文件，未访问真实下载目录或真实客户资料。

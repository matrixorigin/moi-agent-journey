# T092 执行记录

## 来源与范围

已读取本 case 的 `AGENTS.md`、`TASK.md`、`samples/T092/task.md`、`samples/shared/local_rules.md`，以及公共命令规范 `<local-RTK-instructions>`。未读取其他 case、产品答案或历史资料。输入目录为 `samples/T092_duplicates`，输出仅写入 `results/T092`。

## 实际执行

工作目录：`<case-root>/T092`。

读取命令（均成功，exit_code=0）：

```sh
rtk read AGENTS.md
rtk read TASK.md
rtk read <local-RTK-instructions>
rtk read samples/T092/task.md
rtk read samples/shared/local_rules.md
```

哈希清点实际以 `rtk proxy python3 - <<'PY'` 执行以下 Python 代码，exit_code=0：

```python
from pathlib import Path
import hashlib,json
root=Path('samples/T092_duplicates')
files=sorted(p for p in root.rglob('*') if p.is_file())
print('directory_exists:',root.is_dir())
for p in files:
 b=p.read_bytes()
 print(json.dumps({'path':p.as_posix(),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()},ensure_ascii=False))
print('file_count:',len(files))
```

真实回执：目录存在，文件数 3；`a.txt`、`b.txt` 各 11 字节且 SHA-256 均为 `abb7f0ae43ba52cc56233a5ecb4dfa11765f26b1282a18346d811b6a85af19c1`；`c.txt` 为 10 字节，哈希为 `1170c8939638387ed45a0d39fa66b9cf4302208f2192e7d2ffefb1b9e2a620af`。

随后实际执行第二个 `rtk proxy python3` heredoc：递归读取文件、计算相同清单，按 SHA-256 分组，对每个重复组使用 `Path(...).read_bytes() == Path(...).read_bytes()` 逐字节断言；写入 `result.md`、`hash_manifest.json`，重新读取全部源文件并执行 `assert after == rows`，将回执写入 `verification.json`。命令 exit_code=0，真实输出为：

```json
{"source_file_count": 3, "source_total_bytes": 32, "duplicate_group_count": 1, "duplicate_files": 2, "extra_copy_bytes": 11, "byte_comparison_passed": true, "source_manifest_unchanged": true}
```

## 计算与核对口径

以文件全部原始字节为内容，保留大小写、换行和空白；SHA-256 相同且逐字节一致才报告重复。额外副本字节数按每组 `(文件数 - 1) × 单文件大小` 计算，为 11 字节，未实际释放空间。生成报告后再次核对源路径、大小及 SHA-256，与初次清单一致；没有删除、移动或修改源文件。

## 产物与限制

产物：`result.md`、`hash_manifest.json`、`verification.json`、本 `execution.md`。最终另以 Python 重算源清单并核对产物存在和重复内容，真实回执见最终工具输出。未完成项：无。范围仅为指定样例目录，结论不外推至其他目录。

# T091 目录分类整理结果

已对指定合成目录中的 3 个文本文件按实际正文完成分类，分类副本保存在 `organized/`。原目录的 3 个源文件全部保留。

| 文件 | 分类目录 | 正文依据 |
|---|---|---|
| invoice_20261001.txt | organized/财务票据/ | invoice amount 100 |
| project_notes.txt | organized/项目资料/ | project A notes |
| meeting_20261002.txt | organized/会议记录/ | meeting about price |

执行前已输出并保存 `move_preview.md`，逐项原路径与目标路径见 `mapping.json`。实际复制 3 个文件，共 52 字节；逐文件 SHA-256 与源文件一致，核对结果见 `verification.json`。

限制：任务要求移动整理，但同时要求不得删除源文件、只向 results/T091 写入。因此实际采用保留源文件的分类复制，未移动或改写 samples/T091_downloads。源目录原地移动整理未执行。

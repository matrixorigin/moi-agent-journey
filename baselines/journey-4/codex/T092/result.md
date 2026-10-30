# 重复文件报告 T092

对 `samples/T092_duplicates` 递归检查了全部 3 个文件，共 32 字节。按完整原始字节计算 SHA-256，不做文本解码、换行或空白归一化；相同哈希组进一步进行了逐字节比对。

发现 **1 组内容重复**，涉及 2 个文件；其中额外副本为 1 个，共 11 字节。仅报告，不执行删除或修改。

| 重复组 | 文件 | 文件大小 | SHA-256 |
|---|---|---:|---|
| 1 | `samples/T092_duplicates/a.txt` | 11 字节 | `abb7f0ae43ba52cc56233a5ecb4dfa11765f26b1282a18346d811b6a85af19c1` |
| 1 | `samples/T092_duplicates/b.txt` | 11 字节 | `abb7f0ae43ba52cc56233a5ecb4dfa11765f26b1282a18346d811b6a85af19c1` |

该组逐字节比对结果一致。

`samples/T092_duplicates/c.txt` 为独立内容，10 字节，SHA-256 为 `1170c8939638387ed45a0d39fa66b9cf4302208f2192e7d2ffefb1b9e2a620af`。

完整清单见同目录 `hash_manifest.json`。全部 3 个源文件保留；执行后重新读取并核对路径、大小及哈希均未改变。无未完成项。

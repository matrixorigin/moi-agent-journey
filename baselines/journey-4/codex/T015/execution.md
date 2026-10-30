# T015 执行记录

执行日期：2026-10-29（Asia/Shanghai）。只读取本 case 任务、输入与明确引用的公共 RTK 规则；未读取父目录、其他 case、记忆、其他产品回答或评分；未委派。

## 实际本地命令与回执

工作目录：`<case-root>/T015`。

1. `rtk read AGENTS.md` → exit 0；要求仅当前任务、shell 以 rtk 开头、仅写 case 副本。
2. `rtk read TASK.md` → exit 0；要求逐项官方证据、查询日期，交付 result.md 与 execution.md。
3. `rtk read <local-RTK-instructions>` → exit 0；规定 shell 均以 rtk 开头。
4. `rtk read samples/T015/task.md` → exit 0；确认输入与交付目录。
5. `rtk read samples/shared/articles.md` → exit 0；读取三项待核查陈述及 A–E 合成材料。
6. `rtk proxy python3 - <<'PY' ... PY` → 用 pathlib 实际创建 results/T015 并写入本记录与 result.md，脚本打印各文件字节数。写入回执见下文。

## 实际联网查询

使用 web.run 搜索与打开官方网页。查询串按实际执行顺序记录：

- `site.matrixorigin.io MatrixOne open source Apache 2.0 database`
- `site.docs.matrixorigin.cn 查询 延迟 性能 MatrixOne`
- `site.dify.ai self hosted enterprise pricing knowledge base`
- `site.docs.matrixorigin.cn "Query OK" "sec" SELECT`
- `site.github.com/langgenius/dify "multi-tenant" "license"`
- `site.docs.matrixorigin.cn "select" "0.01 sec"`
- `site.docs.matrixorigin.cn "explain analyze" "Time"`

搜索结果出现第三方页面，但判断仅采用以下已实际打开的官方材料：

| 官方来源 | 工具读取回执及证据位置 |
|---|---|
| https://github.com/matrixorigin/matrixone | 成功打开，636 行；README License 节 581–583 行，Apache License 2.0；仓库为数据库源码。 |
| https://docs.matrixorigin.cn/mo/en/v26.4.2.5/MatrixOne/Performance-Tuning/explain/explain-walkthrough.html | 搜索返回正文并成功打开，2167 行；EXPLAIN ANALYZE 部分解释实际测时，示例含 timeConsumed=6643 us。 |
| https://docs.matrixorigin.cn/moi/zh/5.0/guides/matrixone/monitoring.html | 成功打开，268 行；查询延迟为平均每条 SQL 执行时间，页面标明最后更新 2026-09-09。 |
| https://dify.ai/pricing | 成功打开，289 行；190–221 行 Self-Hosted & Enterprise，Community 免费单工作区，Enterprise Custom。该页也列知识库功能，证明反例与知识库类别相关。 |
| https://github.com/langgenius/dify/blob/main/LICENSE | 成功打开，233 行；214–218 行，多租户授权与标识条件。 |

附加已打开官网 https://www.matrixorigin.io/matrixone 的信息来自搜索结果，未作为结果正文的核心证据。执行中使用 find 定位 Apache、Self-Hosted、Multi-tenant，再打开价格与许可相关段落。所有工具均有实际文本返回；未遇访问失败。

## 核对口径与限制

- 三项陈述逐一保留，不用内部合成准确率数据推导数据库授权、性能承诺或行业部署政策。
- 6643 微秒换算为 6.643 毫秒；这是官方示例中的节点耗时，不冒称本次测得的端到端延迟。
- 第三项用官方自托管版本与许可反例否定全称断言；未进行行业穷举，未将云配额套用到自托管。
- 没有运行数据库、部署 Dify、购买服务或发出外部消息；任务无需这些操作。
- 未完成项：无。实际性能基准及全部产品普查不在本次公开陈述核查范围内。

## 文件写入及核对回执

写入脚本 exit 0，实际输出：
```text
results/T015/result.md: 2467 bytes, UTF-8 readable=True
results/T015/execution.md: 3546 bytes, UTF-8 readable=True
```
追加本回执后重新读取两个文件，确认正文包含三个查询日期与三个官方证据判断，两个交付均非空。

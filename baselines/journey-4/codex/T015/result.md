# 三项公开陈述事实核查

查询日期：2026-10-29（Asia/Shanghai）。核查对象为 `samples/shared/articles.md` 末尾三项公开陈述；材料 A–E 是合成内部试验，不能充当公开产品承诺的官方证据。

| 原陈述 | 判断 | 官方证据与解释 | 查询日期 |
|---|---|---|---|
| MatrixOne 是开源数据库 | 属实 | [MatrixOrigin 官方源码仓库](https://github.com/matrixorigin/matrixone)公开数据库源码，并在 README 的 License 节明确采用 Apache License 2.0。该结论针对 MatrixOne 数据库，不能扩展为其所有云服务或商业产品均免费。 | 2026-10-29 |
| 它能保证所有查询零延迟 | 不成立，属于无依据的绝对化承诺 | [MatrixOne 官方性能调优文档](https://docs.matrixorigin.cn/mo/en/v26.4.2.5/MatrixOne/Performance-Tuning/explain/explain-walkthrough.html)说明 EXPLAIN ANALYZE 会执行查询并测量执行时间，示例含 `timeConsumed=6643 us`（6.643 毫秒）的非零节点耗时；[官方监控文档](https://docs.matrixorigin.cn/moi/zh/5.0/guides/matrixone/monitoring.html)也将查询延迟定义为平均每条 SQL 的执行时间。所查官方资料未给出“所有查询零延迟”保证；低延迟或显示舍入后的 0.00 秒都不能推出真实零延迟。 | 2026-10-29 |
| 所有知识库产品都支持无限免费私有部署 | 错误，可由官方反例推翻 | 具有知识库能力的 Dify 在[官方价格页](https://dify.ai/pricing)将自托管 Community 列为免费、单工作区，Enterprise 则为定制商务方案，包含多工作区及商业授权；[官方 LICENSE](https://github.com/langgenius/dify/blob/main/LICENSE)进一步规定多租户环境须取得书面授权，并保留前端标识要求。因此“所有”“无限”“免费”不能同时普遍成立。这里使用的是自托管条款，未把云版文档配额误当作私有部署限制。 | 2026-10-29 |

建议替换为：“MatrixOne 是采用 Apache License 2.0 的开源数据库。查询性能应结合工作负载、数据量、硬件与实测结果说明。知识库产品能否私有部署、免费范围及使用限制，应逐一查看相应版本的官方许可与价格说明。”

边界：此次完成公开资料核查，未运行数据库性能测试，也未穷举所有知识库产品；第三项为全称命题，一个官方反例即可否定。网页与 main 分支可能更新，以上为查询当日所见。

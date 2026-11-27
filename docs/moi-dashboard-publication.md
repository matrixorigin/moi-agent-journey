# J2 报告发布为 MOI 原生数据看板

将报告中的表格数据通过 MOI Product API 写入独立数据库，创建原生看板和图表，并逐项回读实际结果。不是上传 HTML，也不执行 case 或改变评分。源码不包含生产工作区、库名、看板 ID、凭据或真实运行数据。

## 准备表数据

已有按 `j2-dashboard-tables-v1` 生成的 `dashboard-tables.json` 可直接发布。J2 评分 PR 的 `comparison.json` 也可离线转换：

```sh
python3 -m journey.moi_report prepare \
  --comparison /path/to/comparison.json --output /path/to/prepared
```

这会生成 `dashboard-tables.json`，不连接 MOI。可选的 v2、路径评分和低分归因输入沿用既有报告表结构，缺失时不创建对应空图表。

## 发布

```sh
export MOI_BASE_URL=https://moi.matrixorigin.cn/newmoi
# 在已有安全配置的环境提供 MOI_PAT；不要写入代码或仓库。
python3 -m journey.moi_report publish \
  --input /path/to/dashboard-tables.json \
  --workspace-id '<明确选择的工作区 UUID>' \
  --database-name '<新的唯一报告库名>' \
  --title '<报告标题>' \
  --output /private/path/publication-ledger.json
```

新发布会：

1. 校验选定工作区为 ACTIVE，通过 `X-Workspace-ID` 将 Catalog 请求限定到同一工作区。
2. 在 `default` Catalog 创建新数据库，按各表实际字段建表、每 80 行一批插入。
3. 用额外 NULL 标记列查询回读，区分 NULL 和字符串 `"NULL"`，逐行核对输入。
4. 创建看板及版本化图表定义，等待并检查每个图表的实际执行结果和预期行数。
5. 在私有 ledger 中记录资源 ID、提交／接收阶段和已验证进度。ledger 不应提交到公开仓库。

图表涵盖指标、模块分与覆盖、case 明细、耗时、失分项、来源和可选的 v2／路径／归因视图。配置只保存布局和手动刷新，不代表真实 UI 的视觉／交互已经验收。发布成功不等于所有被测 case 通过。

## 恢复与边界

仅在同一输入 scope 下恢复已保存 ledger：

```sh
python3 -m journey.moi_report publish \
  --input /path/to/dashboard-tables.json --resume \
  --workspace-id '<原工作区 UUID>' --output /private/path/publication-ledger.json
```

恢复会核对原数据库、表、图表身份，已完成阶段不会重复执行。数据库名被占用且没有对应 ledger 时停止。表字段或行数变化应使用新库，不在旧报告表中追加新口径结果。

请求在写入提交后失联时，**不能默认服务端没有写入**。先按 ledger 读取原对象并核对实际行数；本实现不会自动重放不确定写入。源表被替换、图表来源失效、UI 视觉和完整 HTML 等效属于另外的验收范围。

`tests/test_moi_report.py` 用模拟 Product API 覆盖完整发布、重复恢复、scope／schema 拒绝、NULL 保真和离线投影；实际 MOI 验证应使用独立资源和最小合成数据。

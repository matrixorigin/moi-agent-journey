# 执行记录

## 来源

- 本 case AGENTS.md、TASK.md、samples/T088/task.md。
- 公共命令规范 <local-RTK-instructions>。
- 业务流程 samples/repo/manual.md；业务数据 samples/shared/sales.csv。
- 未读取其他 case、父目录答案、历史会话或记忆；未访问客户私有资料；未发信或发布。

## 实际命令与回执

通过 rtk read 读取上述材料；通过 rtk proxy python3 heredoc 在 results/T088 下创建脚本、验证器与文档。所有写入均在该交付目录。

运行 `rtk proxy python3 results/T088/verify.py`，退出码 0。验证器用 subprocess 参数数组调用 Python，以下记录为实际执行捕获，不是示例。验证器第二次执行同样退出码 0。

### 调用 1

```text
/Library/Developer/CommandLineTools/usr/bin/python3 <case-root>/T088/results/T088/automate_sales.py --input <case-root>/T088/samples/shared/sales.csv --output-dir <case-root>/T088/results/T088/dry_run_no_artifacts --dry-run
returncode=0
{
  "input": "<case-root>/T088/samples/shared/sales.csv",
  "input_sha256": "1603c345e4e3448ad70e2dffec27a657f83e4e51805a6a63585b742043e68d9a",
  "input_rows": 16,
  "duplicate_rows": 0,
  "non_paid_rows": 1,
  "invalid_rows": 0,
  "included_rows": 15,
  "customer_count": 6,
  "total_amount": "3300",
  "rules": [
    "deduplicate order_id keeping first before paid filter",
    "status equals paid",
    "sum amount by customer_id using Decimal",
    "exclude invalid identifiers or non-finite amount and report them"
  ],
  "dry_run": true,
  "output_dir": "<case-root>/T088/results/T088/dry_run_no_artifacts",
  "planned_files": [
    "summary.csv",
    "exceptions.csv",
    "report.json"
  ]
}
stderr=''
```

### 调用 2

```text
/Library/Developer/CommandLineTools/usr/bin/python3 <case-root>/T088/results/T088/automate_sales.py --input <case-root>/T088/samples/shared/sales.csv --output-dir <case-root>/T088/results/T088/data
returncode=0
{
  "input": "<case-root>/T088/samples/shared/sales.csv",
  "input_sha256": "1603c345e4e3448ad70e2dffec27a657f83e4e51805a6a63585b742043e68d9a",
  "input_rows": 16,
  "duplicate_rows": 0,
  "non_paid_rows": 1,
  "invalid_rows": 0,
  "included_rows": 15,
  "customer_count": 6,
  "total_amount": "3300",
  "rules": [
    "deduplicate order_id keeping first before paid filter",
    "status equals paid",
    "sum amount by customer_id using Decimal",
    "exclude invalid identifiers or non-finite amount and report them"
  ],
  "dry_run": false,
  "output_dir": "<case-root>/T088/results/T088/data",
  "planned_files": [
    "summary.csv",
    "exceptions.csv",
    "report.json"
  ]
}
stderr=''
```

### 调用 3

```text
/Library/Developer/CommandLineTools/usr/bin/python3 <case-root>/T088/results/T088/automate_sales.py --input <case-root>/T088/samples/shared/sales.csv --output-dir <case-root>/T088/results/T088/data
returncode=0
{
  "input": "<case-root>/T088/samples/shared/sales.csv",
  "input_sha256": "1603c345e4e3448ad70e2dffec27a657f83e4e51805a6a63585b742043e68d9a",
  "input_rows": 16,
  "duplicate_rows": 0,
  "non_paid_rows": 1,
  "invalid_rows": 0,
  "included_rows": 15,
  "customer_count": 6,
  "total_amount": "3300",
  "rules": [
    "deduplicate order_id keeping first before paid filter",
    "status equals paid",
    "sum amount by customer_id using Decimal",
    "exclude invalid identifiers or non-finite amount and report them"
  ],
  "dry_run": false,
  "output_dir": "<case-root>/T088/results/T088/data",
  "planned_files": [
    "summary.csv",
    "exceptions.csv",
    "report.json"
  ]
}
stderr=''
```

### 调用 4

```text
/Library/Developer/CommandLineTools/usr/bin/python3 <case-root>/T088/results/T088/automate_sales.py --input <case-root>/T088/samples/shared/sales.csv --output-dir <case-root>/T088/results/T088/data --dry-run
returncode=0
{
  "input": "<case-root>/T088/samples/shared/sales.csv",
  "input_sha256": "1603c345e4e3448ad70e2dffec27a657f83e4e51805a6a63585b742043e68d9a",
  "input_rows": 16,
  "duplicate_rows": 0,
  "non_paid_rows": 1,
  "invalid_rows": 0,
  "included_rows": 15,
  "customer_count": 6,
  "total_amount": "3300",
  "rules": [
    "deduplicate order_id keeping first before paid filter",
    "status equals paid",
    "sum amount by customer_id using Decimal",
    "exclude invalid identifiers or non-finite amount and report them"
  ],
  "dry_run": true,
  "output_dir": "<case-root>/T088/results/T088/data",
  "planned_files": [
    "summary.csv",
    "exceptions.csv",
    "report.json"
  ]
}
stderr=''
```

### 调用 5

```text
/Library/Developer/CommandLineTools/usr/bin/python3 <case-root>/T088/results/T088/automate_sales.py --input <case-root>/T088/results/T088/verification_fixture.csv --output-dir <case-root>/T088/results/T088/fixture_output
returncode=0
{
  "input": "<case-root>/T088/results/T088/verification_fixture.csv",
  "input_sha256": "7f7e45b7ffa51bfe52a5589b1f5087cf2952d4c1ac4244349b0f4c08752abb04",
  "input_rows": 8,
  "duplicate_rows": 2,
  "non_paid_rows": 1,
  "invalid_rows": 3,
  "included_rows": 2,
  "customer_count": 1,
  "total_amount": "0.3",
  "rules": [
    "deduplicate order_id keeping first before paid filter",
    "status equals paid",
    "sum amount by customer_id using Decimal",
    "exclude invalid identifiers or non-finite amount and report them"
  ],
  "dry_run": false,
  "output_dir": "<case-root>/T088/results/T088/fixture_output",
  "planned_files": [
    "summary.csv",
    "exceptions.csv",
    "report.json"
  ]
}
stderr=''
```

## 数据核对与限制

```json
{
  "source_sha256_before": "1603c345e4e3448ad70e2dffec27a657f83e4e51805a6a63585b742043e68d9a",
  "source_sha256_after": "1603c345e4e3448ad70e2dffec27a657f83e4e51805a6a63585b742043e68d9a",
  "fresh_dry_run_tree_unchanged": true,
  "fresh_dry_run_output_absent": true,
  "existing_dry_run_tree_unchanged": true,
  "formal_rerun_byte_identical": true,
  "sales_expected_summary_verified": true,
  "fixture_dedupe_before_filter_verified": true,
  "fixture_decimal_sum_verified": true,
  "fixture_invalid_values_reported": true,
  "fixture_input_unchanged": true,
  "output_sha256": {
    "exceptions.csv": "0b673841854fe3dbc0fc5c34aad8ce6baefc585db5d17b87bb910ab54fe171a0",
    "report.json": "7326004b6d3a23103a80dd852d423706726e188cf88c5506eb5ddc3e41ee8eb9",
    "summary.csv": "1a798f15ec0b8740d9beabbddb4b34851ba6af8d1b52b86875cd02bec44797f4"
  }
}
```

另执行 `rtk read results/T088/data/summary.csv results/T088/data/exceptions.csv`，退出码 0；确认六行客户汇总及订单 OX 的 cancelled 异常行。

dry-run 的不写入断言比较整个 results/T088 文件树（文件内容 SHA-256 与目录成员）；验证器在比较完成后才写自己的 verification.json。dry-run 标准输出被验证器捕获用于审计，不是脚本自行落盘。输入前后以 SHA-256 核对。

夹具独立置于 verification_fixture.csv，验证输出位于 fixture_output，不混入真实销售汇总。验证器可复跑；脚本单进程、全量内存执行，各文件独立原子替换，未验证并发写同目录或超大输入。无未完成的任务要求。

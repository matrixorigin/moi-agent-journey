# 执行记录

## 来源与边界

读取本 case 的 `AGENTS.md`、`TASK.md`、`samples/T089/task.md`、`samples/repo/page.html`，以及 AGENTS 指定的 `<local-RTK-instructions>`。目录清单仅用于确认输入；未读取其他样例文件内容、其他 case、历史或评分资料。全部产物写在 `results/T089`。

## 实际操作与回执

所有 shell 命令以 `rtk` 开头：

1. `rtk proxy cat <local-RTK-instructions>`；`rtk proxy cat AGENTS.md TASK.md`；`rtk proxy cat samples/T089/task.md samples/repo/page.html`；`rtk proxy ls -la samples/repo`：退出码均为 0，确认原页面使用 10,000 次 `innerHTML +=`。
2. `rtk proxy python3 -c ...` 检查可执行工具：系统 PATH 无 chromium/google-chrome，Python 无 playwright；确认 `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` 存在。
3. `rtk proxy python3 - <<'PY' ... PY` 创建原版副本、修复副本、实际可运行的 `benchmark.py`。
4. 首次 `rtk proxy python3 results/T089/benchmark.py`：退出码 1，结果提取正则误取脚本中的 HTML 字符串，JSON 解码失败；修正为读取最终渲染的结果元素后重新完整运行。该失败轮未纳入数据。
5. 第二次 `rtk proxy python3 results/T089/benchmark.py`：退出码 0，六次运行数据如下。每次启动独立 headless Chrome 进程，交替测量 before/after，顺序三轮。脚本保留完整 Chrome 参数、计时代码及正确性断言。Chrome stderr 分别保存为 `chrome-*-*.stderr.log`。

```json
{
  "metric": "synchronous list construction in ms; performance.now around original inline script, excludes paint",
  "platform": "macOS-15.5-arm64-arm-64bit",
  "chrome": "Google Chrome 156.0.8078.48",
  "runs": [
    {
      "variant": "before",
      "run": 1,
      "duration_ms": 9955,
      "count": 10000,
      "all_orders_correct": true,
      "first": "订单 0",
      "last": "订单 9999"
    },
    {
      "variant": "after",
      "run": 1,
      "duration_ms": 2.199999988079071,
      "count": 10000,
      "all_orders_correct": true,
      "first": "订单 0",
      "last": "订单 9999"
    },
    {
      "variant": "before",
      "run": 2,
      "duration_ms": 10287.899999976158,
      "count": 10000,
      "all_orders_correct": true,
      "first": "订单 0",
      "last": "订单 9999"
    },
    {
      "variant": "after",
      "run": 2,
      "duration_ms": 2.299999952316284,
      "count": 10000,
      "all_orders_correct": true,
      "first": "订单 0",
      "last": "订单 9999"
    },
    {
      "variant": "before",
      "run": 3,
      "duration_ms": 10133.200000047684,
      "count": 10000,
      "all_orders_correct": true,
      "first": "订单 0",
      "last": "订单 9999"
    },
    {
      "variant": "after",
      "run": 3,
      "duration_ms": 2.300000011920929,
      "count": 10000,
      "all_orders_correct": true,
      "first": "订单 0",
      "last": "订单 9999"
    }
  ],
  "median_ms": {
    "before": 10133.200000047684,
    "after": 2.299999952316284
  },
  "ratio_before_over_after": 4405.739221795522
}
```

6. 本记录生成时执行 Python 断言：原版副本与输入逐字节一致；测量记录恰为六条，全部数量和逐项文本检查正确。生成 `change.diff` 和 `verification.json` 作为文件核对记录。

## 测量口径与限制

起点位于原内联脚本前；终点位于紧接的后置脚本第一句，正确性检查在计时结束后执行。测试页 `benchmark-before.html`、`benchmark-after.html` 可检查实际注入。页面无外部资源。

浏览器为本机安装版本（见上述 JSON），无 CPU 降速或缓存控制；三轮样本不代表统计显著性，也没有测量 paint、LCP、INP、页面整体加载及用户操作。浏览器临时 profile 在完成后已删除，其余复现资料保留。本任务要求的文件修复、前后测量及内容核对均已实际执行；未完成真实设备和端到端性能评估。

# QuoteCalc 仓库说明

QuoteCalc 是一个 Python 3 标准库报价计算示例，无第三方包或外部账号依赖。核心函数 `total(qty, price, discount=0)` 接收数量、单价和折扣百分比，拒绝负数量或负单价，并计算报价。当前入口只打印一个示例结果；仓库没有实现 HTTP 服务或数据库读写流程。

## 目录与文件

| 路径（相对 samples/repo） | 用途 |
| --- | --- |
| README.md | 项目简介、运行与测试命令 |
| app.py | 报价函数与直接运行入口 |
| tests/test_app.py | unittest 测试，仅验证 `total(2,100)==200` |
| schema.sql | 定义 orders（订单）和 items（明细）两张表；代码未引用 |
| sample.db | 随附数据库文件，本任务未读取内部数据 |
| api.md | 报价接口约定：POST /v1/quotes，Bearer 认证、JSON 参数、200/400/401/429 响应；未发现对应服务实现 |
| manual.md | 独立的订单 CSV 人工汇总流程说明，未发现对应实现 |
| page.html | 采购清单示例页面，以 JavaScript 追加 10,000 个订单条目 |
| change.diff | 报价公式从普通除法改成整除的变更片段 |
| error.txt | 描述 10% 折扣错误的附属问题材料，并非本任务的修复指令 |
| request.log | 随附上传/解析失败及另一任务完成的日志样例，非本次运行日志 |

## 启动与测试

在 `samples/repo` 目录中执行：

```sh
python3 app.py
python3 -m unittest discover -s tests -v
```

不需要安装 requirements 或启动数据库。这里的“启动”是执行命令行示例，程序打印结果后退出；没有常驻端口。

## 实际验证结果

本次环境为 Python 3.9.6。实际通过 RTK 执行入口脚本，退出码 0，输出 `200`。实际运行现有测试，`test_base (test_app.TestTotal)` 通过：`Ran 1 test in 0.000s`，`OK`，退出码 0。

当前公式为 `qty * price * (1 - discount // 100)`。由于 `10 // 100 == 0`，示例 `total(2,100,10)` 返回 200，未应用预期的 10% 折扣（应为 180）。现有测试不传折扣参数，所以无法发现这一问题；也没有覆盖负输入、折扣边界或非法输入类型。API 文档所述正整数数量和 0 到 100 的折扣范围未由当前函数完整校验。

本次按仓库理解任务读取和运行，未修改业务代码、未新增测试、未执行附属 CSV 流程，也未将 API 文档当作已实现接口。任务要求的现有测试和入口运行均已完成。

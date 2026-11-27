# J2 离线检查点评分与运行对比

显式读取已经保存的 Journey-2 结果，生成按能力模块分组的分数、覆盖率和配对对比。这个命令不执行 case，不补做断言，不调用模型，也不写入 MOI。现有 Actions 执行和报告入口保持原行为。

## 使用

输入目录沿用实验 runner 已有格式：

```text
RUN/
  batch-result.json
  results/J2-XX/result.json
  case-runs/J2-XX/attempts/J2-XX/attempt-NN/self-benchmark.json
```

`batch-result.json` 的 `selected_attempt` 决定读取哪份执行规格；缺少结果的 case 仍进入覆盖率分母。比较所需的原 case/suite 哈希来自选中 attempt 的执行规格，缺少身份时不计算直接分差。

```sh
# 单次运行：不虚构旧基线。
python3 -m journey.j2_scoring score \
  --current-run /path/to/RUN --output /path/to/new-report

# 任意两次已保存运行的配对比较。
python3 -m journey.j2_scoring compare \
  --baseline-run /path/to/OLD --current-run /path/to/NEW \
  --output /path/to/new-comparison

# 或自动选择同环境、工作区、模型、套件中最近测试的不同发布版本。
python3 -m journey.j2_scoring compare \
  --history-root /path/to/runs --current-run /path/to/NEW \
  --output /path/to/new-comparison
```

输出 `comparison.json`、`results.csv`、`report.md` 和自包含 `report.html`。HTML 包含模块指标变化、case 分差、耗时配对及累计耗时四类图，支持模块筛选。所有资源随仓库提供，不依赖 CDN。输出目录必须是新目录，旧报告和输入文件不会覆盖。

## 本次评分口径

- 默认清单为 `datasets/journey-2/scoring/agent76-v1.json`：76 条已有 J2 case，保留原来的主模块分类，不包含 J2-126、知识库场景或 J3。清单不绑定私有工作区或服务地址。可以通过 `--suite` 显式选择其他既有 J2 功能 case 的清单。
- case 内检查点等权：通过数／应检查数 × 100，保留两位小数。100 分为 PASS，中间分为 PARTIAL，所有有效检查点都失败且 0 分为 FAIL。
- 模块分为该模块已评分 case 的等权平均，同时展示已评分／总数。未评分不补 0，不计算 MOI 整体质量总分，也不自动判定回退。
- blocked、framework error、not run、timeout、缺少或多出检查点、无有效断言均保留原因并不出分；运行期间明确版本变化也不出分。保留原执行状态和强制断言标记，不把诊断输出补成新通过证据。
- 稳定的逻辑检查点 ID 沿用实验注册表。这里仅纳入当前选集使用的 17 条扩展 case、448 个注册检查点；其他 case 从已有执行断言生成可追溯 ID。注册检查点与原功能断言不是额外相加的维度。
- J2-13 的次优路径半分策略不在本 PR 中实现。输入如果带有该策略或路径系数，保留原成绩字段并标记 `unsupported_scoring_policy`，不将其重新解释为普通等权分。
- 补充 v2 检查、MOI 看板和 J3 裁判不在这个入口中运行。

## 何时计算分差

要求两侧环境、工作区、被测模型一致，原 case 定义哈希一致且匹配当前定义，评分及注册表身份一致，每个 case 运行内版本稳定，且两侧都有有效分数。J2-132 还要求已知且相同的持久化模式、历史上下文。

相同产品版本的两批结果可作为重复运行比较，不能称为版本升级。`--history-root` 会跳过相同发布版本；没有不同版本基线时显示无基线。历史大套件向当前清单投影需显式传 `--allow-suite-projection`，两侧必须拥有当前选集全部结果，其他配对条件仍生效。

JSON 保留每个检查点、原始结果 SHA-256、选用 attempt、未评分原因及不可比原因；图表与 Markdown 的模块分差只使用相同的配对样本。更改评分或 ID 生成逻辑时，应更新 `j2-checkpoints-equal-v1` 的策略版本。

## 验证

`tests/test_j2_score_*.py` 由现有 Python CI 自动发现，`tests_browser/j2-score.test.mjs` 由现有浏览器 CI 执行。测试使用离线样本，不需要 MOI 凭据；历史运行回放在 PR 描述中单独记录，不作为新生产验收。

图表库为 Chart.js 4.4.7，文件已核对该版本 npm 发布包，MIT 许可保存在 `journey/j2_scoring/assets/Chart.js-LICENSE.md`。

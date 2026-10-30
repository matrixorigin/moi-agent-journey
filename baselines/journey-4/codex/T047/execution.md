# 执行记录

## 读取来源

- `AGENTS.md`：限定本 case、命令使用 rtk、不委派及写入范围。
- `<local-RTK-instructions>`：确认 shell 命令以 rtk 开头，使用 `rtk proxy` 执行原生命令。
- `TASK.md` 和 `samples/T047/task.md`：确认任务与交付路径。
- `samples/shared/brand.md`：唯一业务来源；提供三项品牌语气、三项应避免的承诺、三个统一名词、一段技术说明和一段营销草稿。

## 实际执行

依次执行并成功读取（退出码均为 0）：

1. `rtk read AGENTS.md`
2. `rtk read TASK.md`
3. `rtk read <local-RTK-instructions>`
4. `rtk read samples/T047/task.md`
5. `rtk read samples/shared/brand.md`
6. 使用 `rtk proxy python3 -c` 调用 pathlib，实际创建 `results/T047`，以 UTF-8 写入 `result.md` 与本文件。

## 核对口径与限制

无数据计算任务。正文包含语气规则、统一用词、禁用承诺、8组前后修改示例及营销草稿替换稿。将源文中的营销草稿视为待修改材料；其中“三分钟”“永久免费无限量”均无其他资料佐证，因此删除，而非确认为产品政策。示例中的原句除源文原句外，为演示编辑规则构造，不是客户原话。

未查询外部资料、未访问其他 case 或历史记忆、未委派、未发送或公开发布内容。没有实际产品运行或价格政策的验证依据，正文未作此类确认。无未完成交付项。

## 写入后验证回执

实际执行 `rtk proxy python3 -c` 读取两份产物并统计示例表格：两份文件均可按 UTF-8 读回且非空；前后修改示例为 8 组。创建命令及验证命令退出码均为 0。

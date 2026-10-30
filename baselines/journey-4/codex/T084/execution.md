# 执行记录

## 读取来源
- 本 case AGENTS.md、TASK.md；其引用的 <local-RTK-instructions>（shell 前缀要求）。
- samples/T084/task.md。
- samples/repo/api.md、app.py、tests/test_app.py、README.md。
- 为定位附属源码执行过 samples 文件名枚举；未读取其他任务正文、其他产品答案、历史或记忆。

## 实际命令与回执
均在本 case 目录中执行，测试命令在 results/T084 中执行。
1. `rtk proxy cat <local-RTK-instructions>`：退出 0。
2. `rtk proxy cat AGENTS.md TASK.md`：退出 0。
3. `rtk proxy cat samples/T084/task.md samples/repo/api.md`：退出 0。
4. `rtk --files samples`：退出 127，RTK 不支持该调用；未取得文件清单。
5. `rtk proxy find samples -maxdepth 4 -type f`：退出 0，定位 samples/repo/app.py 和 tests/test_app.py。
6. `rtk proxy cat samples/repo/app.py samples/repo/tests/test_app.py samples/repo/README.md`：退出 0。
7. `rtk proxy python3 - <<'PY' ... PY`：使用 shutil.copy2 复制四个相关源文件到交付目录，并写新增测试；退出 0。
8. `rtk proxy python3 -m unittest discover -s tests -v`：真实运行 20 项，失败 11 项，退出 1。
9. `rtk proxy python3 - <<'PY' ... PY`：为保存完整原始日志，再通过 subprocess 运行相同的 RTK 测试命令；同样失败 11 项、退出 1。外层脚本写 test-run.log、verification.json 及两份说明，退出 0。外层成功不代表测试成功。

## 核对与限制
- verification.json 保存实际测试命令、工作目录、Python 版本、退出码，以及原源码/副本和原测试/副本的 SHA-256。两对文件一致。
- 新增 19 项，加原有 1 项共 20 项；9 项成功、11 项失败。测试完整异常堆栈在 test-run.log。
- 未修复实现，不把失败改为预期失败或跳过。原样例内容未改动。
- API 只提供描述，源码仅计算函数，无法直接开展真实 HTTP 集成验证。百分比公式、浮点容差和函数异常约定详见 result.md。
- 不需要外部依赖、账号、网络请求或 token。未完成项为 HTTP 状态码、响应结构、认证和限流验证；输入不具备可运行服务实现。

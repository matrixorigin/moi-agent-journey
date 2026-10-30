# T061 执行记录

## 来源与边界

- 阅读本 case 的 `AGENTS.md`、`TASK.md`、`samples/T061/task.md`。
- 业务输入仅 `samples/shared/meeting.wav`；未读取 `meeting.txt`，未读取其他 case、答案、评分或记忆。
- 录音 SHA-256：`eb2bc3aefbfdd080d541a2168f0c15986a0a286d53642d2e968879b3a47de0b4`。
- `afinfo` 实测：单声道、16,000 Hz、Int16，25.302312 秒，音频数据 809,674 字节。

## 实际执行

以下命令均在本 case 根目录执行，shell 均以 `rtk` 开头。

1. `rtk read <local-RTK-instructions>`：确认命令前缀约束。
2. `rtk proxy sh -c 'cat AGENTS.md; cat TASK.md'`：读取任务边界。
3. `rtk proxy sh -c 'cat samples/T061/task.md; ls -lh samples/shared/meeting.wav; command -v whisper; command -v ffmpeg; command -v python3; command -v afinfo'`：WAV 存在（795K）；无 whisper/ffmpeg 命令，有 Python 和 afinfo。
4. `rtk proxy sh -c 'afinfo samples/shared/meeting.wav; python3 -c "import importlib.util; print({m:importlib.util.find_spec(m) is not None for m in [\"torch\",\"whisper\",\"faster_whisper\",\"speech_recognition\",\"requests\"]})"; command -v uv; command -v brew; command -v swift; env | cut -d= -f1 | rg -i "openai|hugging|token|key"'`：语音识别 Python 包均缺失；uv 可用，未找到相关凭据环境变量名（未输出任何凭据值）。
5. `rtk proxy sh -c 'mkdir -p results/T061; uv venv --python 3.12 results/T061/asr-env; uv pip install --python results/T061/asr-env/bin/python faster-whisper'`：成功创建 Python 3.12.12 环境，安装 faster-whisper 1.2.1、ctranslate2 4.8.4 等共 23 个包。安装耗时约 54 秒。
6. 使用文件工具写入实际运行的 `transcribe.py`；采用 `Systran/faster-whisper-base`，CPU/int8，中文，beam_size=5，不启用 VAD。
7. `rtk proxy sh -c 'HF_HOME="$PWD/results/T061/hf-cache" results/T061/asr-env/bin/python results/T061/transcribe.py > results/T061/asr-run.log 2>&1'`：直接解码 WAV 并执行 ASR；模型下载目录在本交付目录，日志保存在 `asr-run.log`。
8. `rtk proxy sh -c 'shasum -a 256 samples/shared/meeting.wav; date -u +%Y-%m-%dT%H:%M:%SZ'`：记录输入校验和；执行时间为 2026-10-29T10:54:54Z。
9. 原下载日志只有 HF 未认证提示，模型主体长时间保持 0 字节。`rtk proxy curl -I -L --max-time 20 https://huggingface.co/Systran/faster-whisper-base/resolve/main/model.bin` 返回 HTTP 200，文件 145,217,532 字节，确认公开下载链路可达。
10. `rtk proxy sh -c 'pgrep -f "[p]ython results/T061/transcribe.py"'` 定位本次脚本；`rtk proxy kill 79502` 停止停滞进程。随后实际重试：`rtk proxy sh -c 'HF_HUB_DISABLE_XET=1 HF_HOME="$PWD/results/T061/hf-cache" results/T061/asr-env/bin/python results/T061/transcribe.py > results/T061/asr-retry.log 2>&1'`。禁用 Xet 后下载开始正常增长。
11. 过程中仅对本交付目录运行 `tail -c 4000 results/T061/asr-retry.log`、`du -sh results/T061/models` 等检查，未读取文本替代录音。
12. 模型下载完成，首次解码因 av 19.0.2 与 faster-whisper 的调用不兼容而失败：`TypeError: open() got an unexpected keyword argument 'metadata_errors'`（见 `asr-retry.log`）。修改脚本直接用 Python `wave` 解码原始 WAV，校验单声道/16bit/16kHz，转换为 float32 波形。该方法仍直接处理同一录音，不使用任何文字替代。
13. `rtk proxy sh -c 'HF_HUB_DISABLE_XET=1 HF_HOME="$PWD/results/T061/hf-cache" results/T061/asr-env/bin/python results/T061/transcribe.py > results/T061/asr-final.log 2>&1'`：退出码 0，实际生成 `transcript.json`。首轮输出总时段 0–25.36 秒（模型末端略超过音频实际长度），平均 logprob -0.1677。
14. 写入 `verify_audio.py` 并执行：`rtk proxy sh -c 'HF_HUB_DISABLE_XET=1 HF_HOME="$PWD/results/T061/hf-cache" results/T061/asr-env/bin/python results/T061/verify_audio.py > results/T061/verification-run.log 2>&1'`：退出码 0。对 0–8、7–15、14–21、20–25.3023125 秒片段重叠复识，beam_size=10，生成 `verification-transcript.json`，包含分词概率。两轮日期、5万元、8人、扩容未批准、安全审查、演示等内容一致；同音字识别问题仍存在。
15. 最终使用 `rtk proxy sh -c 'python3 -c "import json,pathlib; p=pathlib.Path(\"results/T061\"); a=json.loads((p/\"transcript.json\").read_text()); b=json.loads((p/\"verification-transcript.json\").read_text()); print(\"duration:\",a[\"duration\"],\"segments:\",len(a[\"segments\"]),\"verification_clips:\",len(b)); print(\"result_bytes:\",(p/\"result.md\").stat().st_size,\"execution_bytes:\",(p/\"execution.md\").stat().st_size)"'` 核验 JSON 可解析及正文文件存在：duration 25.3023125、segments 1、verification_clips 4、result_bytes 3142；退出码 0。

## 口径

纪要仅提取录音中出现的讨论、明确决定和行动项。未给出会议日期时不把执行日期当会议日期；相对截止时间保留原话。人名无文字材料佐证时标注同音字不确定。ASR 时间戳仅用于定位，不视为精确人工分段。

## 限制与交付

- 未进行人工听辨；依据实际 ASR 和分段复识输出整理。两次识别使用同一模型，不能作为独立模型交叉确认。
- 人名均标注用字待核对；同音词语境校整逐条在正文披露。没有读取 `meeting.txt`。
- 已交付 `result.md`、本记录、两份原始转写 JSON、两个实际执行脚本和运行日志。模型/环境仅为本次本地运行依赖，不属于纪要正文。
- 不存在已知尚未运行的转写步骤；未完成的是人工听辨和姓名用字核验。

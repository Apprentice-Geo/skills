# audio-transcribe

`audio-transcribe` 用于将本地音频转换为可复用的文字稿和时间戳，也可供总结、字幕制作等后续 Skill 使用。

## 使用边界

- 输入音频必须已存在于本地；本 Skill 不下载媒体，也不修改其他 Skill 的结果。
- 公共结果由 `manifest.json` 和 `transcript.json` 组成，可一起复制或移动，无需携带日志、workspace 或原音频。`manifest.json` 是唯一公开入口，只读且必须通过 `audio_transcribe_contract.load_result()` 验证后读取。
- 文本规范化、公共格式版本、兼容边界和损坏恢复方式见[架构说明](references/ARCHITECTURE.md#公共-contract-与发布)。
- 本地模型必须通过安装身份和基本文件结构校验，详见[模型安装](references/ERROR-HANDLING.md#模型安装)。

## 安装与运行数据

使用 Windows、Python 3.12、`uv` 和 Git。合同包通过 GitHub 固定 tag 安装，首次安装需要访问 GitHub，各 Skill 可独立安装，不依赖相邻目录关系。通用依赖源按官方 PyPI、清华、阿里的顺序配置（`first-index` 策略）；CPU 与 Qwen3-ASR 环境的安装和切换见 [Setup 与依赖](references/ERROR-HANDLING.md#setup-与依赖)。

运行日志、报告、缓存和结果可保存在指定的可写数据目录，默认使用 Skill 根目录。切换位置不会自动迁移历史任务或模型；配置方法见 [SKILL.md](SKILL.md)，写入错误与恢复影响见[错误处理](references/ERROR-HANDLING.md#运行目录与写入失败)。

## 隐私与安全

环境配置需要联网，转写本身完全在本地执行。音频和转写产物可能包含个人信息、机密对话、版权内容或本地路径，分享结果或日志前请先检查。

## 进一步阅读

- [SKILL.md](SKILL.md)：完整使用规则、环境要求、命令和停止条件。
- [references/ARCHITECTURE.md](references/ARCHITECTURE.md)：转写流程、缓存、身份和公开产物设计。
- [references/ERROR-HANDLING.md](references/ERROR-HANDLING.md)：安装、模型、Provider、缓存和产物错误处理。
- [AGENTS.md](AGENTS.md)：开发维护、测试和代码规范。
- [references/DEVELOPMENT.md](references/DEVELOPMENT.md)：暂存目录、依赖兼容、模型身份、alignment 和发布诊断的开发约束。
- [benchmark/README.md](benchmark/README.md)：benchmark 方法、固定测试数据、开发验证和报告说明；普通转写用户无需阅读。

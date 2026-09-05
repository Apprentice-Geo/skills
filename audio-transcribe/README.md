# audio-transcribe

`audio-transcribe` 用于将已经存在于本地的音频转换为可复用的文字稿和时间戳，也可供总结、字幕制作等后续 Skill 使用。

## 使用边界

- 输入音频必须已经存在于本地。
- Skill 不负责下载媒体，也不修改其他 Skill 的结果。
- 所有公开转写文本执行 Unicode NFKC；语言为 `zh` 时再通过 OpenCC `t2s` 转为简体。Provider 原始 chunk 仅保留在内部缓存中。
- 公共结果由 `manifest.json` 和 `transcript.json` 组成，可一起复制或移动，无需携带日志、workspace 或原音频。`manifest.json` 是唯一公开入口，必须通过 `audio-transcribe-contract.load_result()` 验证后读取。
- 契约包 0.2.0 使用公共 schema v2；不兼容旧三文件结果或旧 Python API。重新运行转写命令生成新结果，不手动迁移旧文件。
- 完整有效结果直接复用；生产命令修复损坏结果时，可能在相同音频与配置身份下重新发布不同内容。需要固定历史结果时保存独立 bundle；公共 loader 始终只读。

## 隐私与安全

运行环境配置需要访问网络，转写完全在本地环境中执行，但音频本身可能包含个人信息、机密对话或受版权保护的内容，分享转写文本、时间戳或结果目录前，请确认其中没有不应公开的原始内容、日志或路径信息。

依赖源按官方 PyPI、清华、阿里的顺序配置，并使用 uv 的 `first-index` 策略；国内源作为可信的后续候选源。

## 使用与合同

- [SKILL.md](SKILL.md)：完整使用规则、环境要求、命令和停止条件。
- [references/ARCHITECTURE.md](references/ARCHITECTURE.md)：转写流程、缓存、身份和公开产物设计。
- [references/ERROR-HANDLING.md](references/ERROR-HANDLING.md)：安装、模型、Provider、缓存和产物错误处理。

## 开发与维护

- [AGENTS.md](AGENTS.md)：开发维护、测试和代码规范。
- [benchmark/README.md](benchmark/README.md)：benchmark 方法、固定测试数据、开发验证和报告说明；普通转写用户无需阅读。

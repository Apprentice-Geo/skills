# 开发实现约束

本文面向修改本 Skill 的开发者，保存运行故障中已经确认的实现易错点。普通转写、安装和恢复使用 [SKILL.md](../SKILL.md) 与[错误处理](ERROR-HANDLING.md)；模块职责、数据流及公共合同见[架构](ARCHITECTURE.md)。修改相关实现时按对应章节读取，不要求运行 Skill 时加载全文。

## 暂存目录与日志初始化

Windows 下的本地快照、发布和下载暂存目录使用随机名称加独占 `mkdir` 创建，继承父目录 ACL；不得使用 `mkdtemp`、`TemporaryDirectory` 或 `mkdir(mode=0o700)` 创建这类业务目录，否则可能排除沙箱身份并导致创建后写入失败。现有实现入口为 `scripts/io_utils.py`。该规则不改变 Pytest 的沙箱外执行要求。

日志初始化失败时恢复 logger、warnings hook 和 handler 状态；不继续业务，不回退到未知目录，也不输出未建立的 `Full log` 路径。setup 不为初始化 cache 或日志顺便创建模型或结果目录。

## 依赖导入兼容

安装器、检查器和运行时复用 `scripts/import_compat.py`，在依赖导入前后仅移除已知 SpeechBrain 可选旧路径的懒加载别名，保留真实模块和其他异常。自动语言检测在语言模型加载前处理别名；Qwen 加载入口同样处理，覆盖显式语言和自动检测路径。

检查器保留各模块的独立子进程探测，并额外在同一进程按语言识别依赖、Qwen 依赖的顺序探测。组合失败时 Qwen 不能 ready，即使独立探测全部通过。探测失败保留正在导入的模块、异常类型、缺失模块名和原始异常链，完整 traceback 写入已建立的日志。

必需依赖、可选模块误触发及 CUDA build/runtime 的故障恢复边界见[连续导入兼容](ERROR-HANDLING.md#连续导入兼容)，不得通过安装无关可选模块或吞掉异常消除表面失败。

## 模型身份校验

安装校验要求 `.model_identity.json` 是无重复键且仅包含字符串 repo/revision 的 JSON 对象，并与固定配置完全匹配；必需文件与权重必须存在且非空，indexed safetensors 的索引和全部分片必须合法且位于模型目录内。setup 复用、依赖检查、自动 Provider 候选检查和运行时使用相同规则。模型加载仍负责识别文件内容是否可用。

prepared model 必须携带加载时绑定的身份和配置摘要；缺失或不匹配时停止，不能用当前磁盘 marker 为未知内存模型补认身份。校验范围与安装修复见[模型安装](ERROR-HANDLING.md#模型安装)。

## Alignment 接受与规范化

- 保留 Provider candidate 的原始时间精度。原本 zero-duration 的 item 连同且仅连同归其所有的字符文本一起移除；正时长的亚毫秒 item 保留。没有 owner 的标点和空白应予以保留，除非 accepted chunk 因此只剩这些内容而变为空。
- accepted item 和公共 item 拒绝负数、非有限、重叠、递减、反向或超出 duration 的 timestamp，满足 `0 <= start < end <= duration` 和 `start >= previous_end`；item 文本非空。
- 非 null probability 必须有限且位于 `[0, 1]`；Qwen3-ASR timestamp item 的 probability 必须为 null。
- 除 Qwen adapter 尾部误差处理和合法时间的浮点偏移边界处理外，不得裁剪超出范围的 end time；不得把格式错误的 workspace/公共字段强制转换为字符串或浮点数。
- Provider chunk 文本不执行改写。合并后的 workspace/公共边界统一应用 NFKC，仅对 `zh` 应用 OpenCC `t2s`。规范化和分段的数据流见[架构](ARCHITECTURE.md#alignment-与规范化)。
- Provider 返回意外的结果 shape 时，先检查固定依赖版本和 adapter，再考虑更改公共 artifact schema。不得在公共 artifact 中存储第三方模型对象、原始 Provider response 或大型内部 metadata。

完整结果的非空要求、规范化失败与发布停止边界见[错误处理](ERROR-HANDLING.md#alignment-与空结果)，不得通过放宽接受条件规避失败。

## 恢复与发布诊断

workspace snapshot 已包含规范化文本，公共转换不再次运行 OpenCC。chunk 重建仍执行完整合并和规范化。新接受 candidate 的 zero-duration cleanup 发出聚合警告；chunk cache 不保存 cleanup report，恢复不重放历史警告。pipeline 不新写或删除历史 `progress.json`、`metrics.json`。

成功安装公共 manifest 后才记录发布诊断：相同 digest 的精确恢复为 INFO；不同 digest 的重新发布为 WARNING，包含 `audio_id`、`config_digest` 和旧/新 digest；无有效原 manifest 的发布为 INFO。诊断不包含转写文本，失败不记录发布成功，不增加公共审计字段。发布与回滚协议见[公共 contract 与发布](ARCHITECTURE.md#公共-contract-与发布)。

可以使用精确的 logger/message-prefix filter 过滤已知的嘈杂第三方警告。不得抑制未知警告或异常。

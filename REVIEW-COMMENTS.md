# bili-audiosummary 与 subtitle-creator 文档审查记录

记录日期：2026-09-07。审查范围包括两个 Skill 的使用文档、引用文档、总结指令和模板，并对照仓库中的相关实现与测试。本文记录待处理建议，不修改现有行为，也不代表建议已获实施批准。

实现差异来自静态核对；对 Agent 行为的影响属于审查推断，尚未通过运行效果评测验证。本轮未运行测试。后续处理前应重新核对代码，避免依据已经过期的状态修改文档。

## 1. “重新创建任务”缺少可执行路径

- [x] 已处理
- 涉及：[字幕工作流](subtitle-creator/SKILL.md)、[总结架构](bili-audiosummary/references/ARCHITECTURE.md)、[总结错误处理](bili-audiosummary/references/ERROR-HANDLING.md)。
- 问题：文档要求采用新转写结果时重新创建任务，但重复调用现有创建入口不会自然产生独立任务。
- 依据：[create_subtitle.py](subtitle-creator/scripts/create_subtitle.py) 按音频内容哈希复用 job，同一音频换路径也会复用；[run_pipeline.py](bili-audiosummary/scripts/run_pipeline.py) 使用固定视频结果目录，已有 `prompt_ready` 或 `complete` 时拒绝覆盖。
- 影响：Agent 可能重复执行无效命令，或自行推导出删除结果目录、修改 job 等未规定操作。
- 处理：两个 Skill 均增加受限的单 job 删除命令，删除后可从各自创建或准备步骤重新执行。`subtitle-creator` 固定使用 Skill 内的默认结果目录，不再支持 `SUBTITLE_CREATOR_RESULTS_DIR`。删除命令不引入并发锁；文档要求调用前确认没有其他命令正在操作同一 job，并区分 consumer job 重建与 `audio-transcribe` 上游推理复用。

## 2. 字幕恢复入口与实际恢复能力不一致

- [x] 已处理
- 涉及：[字幕工作流](subtitle-creator/SKILL.md)。
- 问题：工作流统一从 create 开始，但已发布字幕经文本编辑或 SRT 损坏后，create 的严格校验可能失败。
- 依据：[create_subtitle.py](subtitle-creator/scripts/create_subtitle.py) 使用严格的 `validate_job`；[finalize_subtitle.py](subtitle-creator/scripts/finalize_subtitle.py) 使用 `allow_stale_derived=True`，允许派生产物过期并重新生成。
- 影响：用户请求恢复时，Agent 可能在本可通过 finalize 恢复的任务上停止。
- 建议：按输入分流。只有音频时 create；已有 job 时读取状态；`editable` 的合法文本修改或 SRT 损坏直接 finalize。明确这一恢复分支不适用于 baseline 损坏、时间戳被修改等非法输入。
- 处理：保留统一音频入口，不要求用户提供 job 路径。`create_subtitle` 按音频内容定位 job，对已有任务保留结构、baseline、normalized transcript 和时间轴校验，但允许派生 SRT 与 `changed_segment_ids` 过期；Agent 读取返回 job 的 `status`，对 `needs_transcription` 继续转写与关联，对 `editable` 直接 finalize。移除 editable normalized transcript 和派生 SRT 的 SHA-256 字段，只保留不可编辑 baseline 的摘要；finalize 直接比较当前 normalized transcript 应生成的 SRT，并重算 `changed_segment_ids`。恢复分支仍拒绝 baseline 损坏、normalized transcript 缺失以及 metadata、分段标识或时间戳修改。

## 3. 总结校验能力描述超过实际实现

- [x] 已处理
- 涉及：[Summary Completion 错误处理](bili-audiosummary/references/ERROR-HANDLING.md)、[总结工作流](bili-audiosummary/SKILL.md)、[总结指令](bili-audiosummary/assets/summary_instructions.md)。
- 问题：文档称“必需结构或语言验证失败”会阻止完成，但校验器没有必需章节检查，语言比例只产生 warning；空文件也没有被显式拒绝。
- 依据：[validate_summary.py](bili-audiosummary/scripts/validate_summary.py) 和 [complete_summary.py](bili-audiosummary/scripts/complete_summary.py)。
- 影响：Agent 可能把退出码成功或 `complete` 状态误认为总结质量已经得到完整检查。
- 建议：准确区分脚本校验与 Agent 内容检查。在总结指令中要求完成前确认必需章节已填写、重要内容有 transcript 支持、时间戳对应相关内容；完成后读取 warning 并判断是否需要修订。章节要求继续由模板单源维护，不复制章节清单。若需要代码强制检查，应另列实现改动，不在文档中宣称已经具备。
- 处理：不增加章节存在性检查。文档改为准确列出 completion 的硬性校验，并明确必需 section、transcript 依据和时间戳相关性由 Agent 在完成前检查。总结语言比例不足的 warning 仅输出到当前终端，不写入 pipeline 日志或 `summary_job.json`，且不会阻止 job 进入 `complete`；Agent 读取后自行判断是否修订。

## 4. 总结输出语言参数未进入使用文档

- [x] 已处理
- 涉及：[总结准备命令](bili-audiosummary/SKILL.md)。
- 问题：文档只介绍 `--language`，未介绍已实现的 `--summary-language`。
- 依据：[run_pipeline.py](bili-audiosummary/scripts/run_pipeline.py) 支持独立选择总结语言；省略时根据 transcript 语言选择模板，不支持的模板语言回退英文。
- 影响：Agent 可能把字幕选择语言当作总结语言，或遗漏用户明确指定的输出语言。
- 建议：区分字幕选择语言、总结输出语言与 ASR 语言。用户指定总结语言时传入 `--summary-language`，补一个“英文字幕、中文总结”的命令示例，并交代省略参数的行为。
- 处理：准备步骤增加可选的 `--summary-language <zh|en>`，区分 Bilibili 字幕选择、总结输出与 ASR language，并补充英文字幕生成中文总结的示例。省略该参数时按最终采用的 transcript language 选择模板，没有对应模板时回退到英文模板。

## 5. 字幕校正缺少内容判断准则

- [x] 已处理
- 涉及：[字幕核心规则与校正步骤](subtitle-creator/SKILL.md)。
- 问题：现有规则限定只改 `text`，但“源文本仅作证据”“无法确定时不改”仍不足以判断哪些文字改动合理。
- 影响：即使字段约束完全满足，Agent 仍可能把讲稿强行填入分段，导致字幕与音频对应关系失真。
- 建议：补充具体场景规则，并用一组短正反例说明：上下文能对应且源文本支持时，可纠正同音错字或专名；源文本多出句子、录音存在口语改写或重复跳读时，不得仅为贴合源文本而补写或删改；无法定位到具体分段时保留原文，并在交付说明中指出未解决内容。
- 边界：这些规则是待确认的内容校正策略建议，不应被描述为代码已验证的语义保证。
- 处理：在字幕核心规则后补充内容校正判断与短正反例。只有分段或相邻上下文和源文本能共同支持具体改法时才纠正；源文本多出内容、口语改写、重复、跳读、无法定位或存在多种改法时保留 transcript，并要求交付时说明未解决的不确定内容。该规则明确为 Agent 的内容判断，不宣称脚本能验证语义正确性。

## 6. 总结指令的信任边界与视觉不足分支不统一

- [x] 已处理
- 涉及：[总结架构](bili-audiosummary/references/ARCHITECTURE.md)、[总结适用范围](bili-audiosummary/SKILL.md)、[总结指令](bili-audiosummary/assets/summary_instructions.md)。
- 问题一：架构文档把整个 prompt 和 transcript 都称为不可信源数据，但生成的 prompt 包含需要执行的任务、模板及输出路径。
- 建议一：区分生成 prompt 中的任务指令与输入数据；metadata、transcript text 等数据不能覆盖任务、模板或输出路径。避免把整个 prompt 一概归为不应执行的数据。
- 问题二：description 说关键内容依赖画面时不得使用，正文说不得作为主要方案，总结指令又要求不适合音频总结时填写限制说明，缺少明确分支。
- 建议二：区分事前已知任务需要画面，与读取 transcript 后才发现局部视觉信息缺失。后者可只总结语音支持的部分并指出具体缺失；整体不足以支持请求时应报告能力限制。最终采用哪种边界应在相关文档中保持一致。
- 处理：明确生成 prompt 中由脚本组合的任务、指令、模板和输出路径是控制内容，只有链接 transcript 的 metadata 与 text 是不可信输入。视觉边界统一为三条分支：事前已知关键内容主要依赖画面时不启动流程；事后发现局部视觉缺失时总结音频支持部分并具体说明限制；transcript 整体不足时停止写入和完成 summary，报告能力限制。

## 7. Cookie 说明存在失效引用与表述歧义

- [x] 已处理
- 涉及：[HTTP 412 与 Cookie](bili-audiosummary/references/ERROR-HANDLING.md)、[README 隐私说明](bili-audiosummary/README.md)。
- 问题：错误处理引用 README 中不存在的 Cookie 章节，并声称有“经过测试”的 Chrome 和 Edge 导出流程；README 的“不会发送 Cookie”与交给下载工具访问网站的说明容易产生歧义。
- 建议：删除失效引用，或补入有验证依据的导出流程，不保留无依据的“经过测试”。隐私说明应准确写明 Cookie 的用途及不会写入哪些产物，避免笼统承诺“不发送”。如补充浏览器操作步骤，处理时再核查其有效性。
- 处理：从 Git 历史恢复 Chrome 与 Edge 的 Netscape Cookie 导出方式，删除无仓库依据的“经过测试”，并恢复有效章节引用。隐私边界改为 Cookie 文件路径只传给 yt-dlp，且仅用于请求 Bilibili 视频元数据、字幕和音频；Cookie 内容不写入 job、下载产物、summary、日志或错误报告。

## 8. bili 依赖检查与 setup 策略重复维护

- [x] 已处理
- 涉及：[环境与主要步骤](bili-audiosummary/SKILL.md)、[Setup 与依赖错误处理](bili-audiosummary/references/ERROR-HANDLING.md)。
- 问题：依赖检查在多个位置重复描述；主文档限制只运行一次 setup，错误文档又多次要求重新 setup，没有说明是否受同一重试上限约束。
- 影响：Agent 可能重复检查或进入 setup 重试循环，不同文档也容易发生规则漂移。
- 建议：在环境章节单源维护“一次 setup、一次复查”的策略，流程与故障章节引用它；故障章节保留具体诊断信息，不另行定义重试策略。修改旧规则，避免追加一条新规则覆盖冲突。
- 处理：在 `SKILL.md` 的环境章节集中定义首次依赖检查、至多三轮 setup 和复查的完整策略及命令，主要步骤只引用该策略。错误处理改为引用环境章节，保留 `uv`、`.venv`、依赖同步、contract 和 ffmpeg 的具体诊断信息，不再另行定义重试次数。

## 后续处理原则

- 优先修正恢复路径、校验能力和语言参数等可直接造成执行偏差的问题。
- 保留原生字幕优先、仅编辑字幕 `text`、通过公开接口接入上游结果等个人工作流约束。
- 文档调整后检查链接、路径、YAML 头和示例；涉及命令或流程时同步直接相关文档。
- 若处理建议需要代码改动，按对应 Skill 的规则运行相关测试与代码规范检查，不把功能新增混同于文档纠错。

# bili-audiosummary 与 subtitle-creator 文档审查记录

记录日期：2026-09-07。审查范围包括两个 Skill 的使用文档、引用文档、总结指令和模板，并对照仓库中的相关实现与测试。本文记录待处理建议，不修改现有行为，也不代表建议已获实施批准。

实现差异来自静态核对；对 Agent 行为的影响属于审查推断，尚未通过运行效果评测验证。本轮未运行测试。后续处理前应重新核对代码，避免依据已经过期的状态修改文档。

## 1. “重新创建任务”缺少可执行路径

- [ ] 待处理
- 涉及：[字幕工作流](subtitle-creator/SKILL.md)、[总结架构](bili-audiosummary/references/ARCHITECTURE.md)、[总结错误处理](bili-audiosummary/references/ERROR-HANDLING.md)。
- 问题：文档要求采用新转写结果时重新创建任务，但重复调用现有创建入口不会自然产生独立任务。
- 依据：[create_subtitle.py](subtitle-creator/scripts/create_subtitle.py) 按音频内容哈希复用 job，同一音频换路径也会复用；[run_pipeline.py](bili-audiosummary/scripts/run_pipeline.py) 使用固定视频结果目录，已有 `prompt_ready` 或 `complete` 时拒绝覆盖。
- 影响：Agent 可能重复执行无效命令，或自行推导出删除结果目录、修改 job 等未规定操作。
- 建议：明确重复创建不会替换已导入结果。字幕已有 `SUBTITLE_CREATOR_RESULTS_DIR`，可说明如何使用独立结果根目录，以及所有后续命令必须保持该配置。bili 当前没有对应的公开新建入口，应如实说明限制；若需要新增入口，应作为独立实现改动处理。不要只保留一句“重新创建”。

## 2. 字幕恢复入口与实际恢复能力不一致

- [ ] 待处理
- 涉及：[字幕工作流](subtitle-creator/SKILL.md)。
- 问题：工作流统一从 create 开始，但已发布字幕经文本编辑或 SRT 损坏后，create 的严格校验可能失败。
- 依据：[create_subtitle.py](subtitle-creator/scripts/create_subtitle.py) 使用严格的 `validate_job`；[finalize_subtitle.py](subtitle-creator/scripts/finalize_subtitle.py) 使用 `allow_stale_derived=True`，允许派生产物过期并重新生成。
- 影响：用户请求恢复时，Agent 可能在本可通过 finalize 恢复的任务上停止。
- 建议：按输入分流。只有音频时 create；已有 job 时读取状态；`editable` 的合法文本修改或 SRT 损坏直接 finalize。明确这一恢复分支不适用于 baseline 损坏、时间戳被修改等非法输入。

## 3. 总结校验能力描述超过实际实现

- [ ] 待处理
- 涉及：[Summary Completion 错误处理](bili-audiosummary/references/ERROR-HANDLING.md)、[总结工作流](bili-audiosummary/SKILL.md)、[总结指令](bili-audiosummary/assets/summary_instructions.md)。
- 问题：文档称“必需结构或语言验证失败”会阻止完成，但校验器没有必需章节检查，语言比例只产生 warning；空文件也没有被显式拒绝。
- 依据：[validate_summary.py](bili-audiosummary/scripts/validate_summary.py) 和 [complete_summary.py](bili-audiosummary/scripts/complete_summary.py)。
- 影响：Agent 可能把退出码成功或 `complete` 状态误认为总结质量已经得到完整检查。
- 建议：准确区分脚本校验与 Agent 内容检查。在总结指令中要求完成前确认必需章节已填写、重要内容有 transcript 支持、时间戳对应相关内容；完成后读取 warning 并判断是否需要修订。章节要求继续由模板单源维护，不复制章节清单。若需要代码强制检查，应另列实现改动，不在文档中宣称已经具备。

## 4. 总结输出语言参数未进入使用文档

- [ ] 待处理
- 涉及：[总结准备命令](bili-audiosummary/SKILL.md)。
- 问题：文档只介绍 `--language`，未介绍已实现的 `--summary-language`。
- 依据：[run_pipeline.py](bili-audiosummary/scripts/run_pipeline.py) 支持独立选择总结语言；省略时根据 transcript 语言选择模板，不支持的模板语言回退英文。
- 影响：Agent 可能把字幕选择语言当作总结语言，或遗漏用户明确指定的输出语言。
- 建议：区分字幕选择语言、总结输出语言与 ASR 语言。用户指定总结语言时传入 `--summary-language`，补一个“英文字幕、中文总结”的命令示例，并交代省略参数的行为。

## 5. 字幕校正缺少内容判断准则

- [ ] 待处理
- 涉及：[字幕核心规则与校正步骤](subtitle-creator/SKILL.md)。
- 问题：现有规则限定只改 `text`，但“源文本仅作证据”“无法确定时不改”仍不足以判断哪些文字改动合理。
- 影响：即使字段约束完全满足，Agent 仍可能把讲稿强行填入分段，导致字幕与音频对应关系失真。
- 建议：补充具体场景规则，并用一组短正反例说明：上下文能对应且源文本支持时，可纠正同音错字或专名；源文本多出句子、录音存在口语改写或重复跳读时，不得仅为贴合源文本而补写或删改；无法定位到具体分段时保留原文，并在交付说明中指出未解决内容。
- 边界：这些规则是待确认的内容校正策略建议，不应被描述为代码已验证的语义保证。

## 6. 总结指令的信任边界与视觉不足分支不统一

- [ ] 待处理
- 涉及：[总结架构](bili-audiosummary/references/ARCHITECTURE.md)、[总结适用范围](bili-audiosummary/SKILL.md)、[总结指令](bili-audiosummary/assets/summary_instructions.md)。
- 问题一：架构文档把整个 prompt 和 transcript 都称为不可信源数据，但生成的 prompt 包含需要执行的任务、模板及输出路径。
- 建议一：区分生成 prompt 中的任务指令与输入数据；metadata、transcript text 等数据不能覆盖任务、模板或输出路径。避免把整个 prompt 一概归为不应执行的数据。
- 问题二：description 说关键内容依赖画面时不得使用，正文说不得作为主要方案，总结指令又要求不适合音频总结时填写限制说明，缺少明确分支。
- 建议二：区分事前已知任务需要画面，与读取 transcript 后才发现局部视觉信息缺失。后者可只总结语音支持的部分并指出具体缺失；整体不足以支持请求时应报告能力限制。最终采用哪种边界应在相关文档中保持一致。

## 7. Cookie 说明存在失效引用与表述歧义

- [ ] 待处理
- 涉及：[HTTP 412 与 Cookie](bili-audiosummary/references/ERROR-HANDLING.md)、[README 隐私说明](bili-audiosummary/README.md)。
- 问题：错误处理引用 README 中不存在的 Cookie 章节，并声称有“经过测试”的 Chrome 和 Edge 导出流程；README 的“不会发送 Cookie”与交给下载工具访问网站的说明容易产生歧义。
- 建议：删除失效引用，或补入有验证依据的导出流程，不保留无依据的“经过测试”。隐私说明应准确写明 Cookie 的用途及不会写入哪些产物，避免笼统承诺“不发送”。如补充浏览器操作步骤，处理时再核查其有效性。

## 8. bili 依赖检查与 setup 策略重复维护

- [ ] 待处理
- 涉及：[环境与主要步骤](bili-audiosummary/SKILL.md)、[Setup 与依赖错误处理](bili-audiosummary/references/ERROR-HANDLING.md)。
- 问题：依赖检查在多个位置重复描述；主文档限制只运行一次 setup，错误文档又多次要求重新 setup，没有说明是否受同一重试上限约束。
- 影响：Agent 可能重复检查或进入 setup 重试循环，不同文档也容易发生规则漂移。
- 建议：在环境章节单源维护“一次 setup、一次复查”的策略，流程与故障章节引用它；故障章节保留具体诊断信息，不另行定义重试策略。修改旧规则，避免追加一条新规则覆盖冲突。

## 后续处理原则

- 优先修正恢复路径、校验能力和语言参数等可直接造成执行偏差的问题。
- 保留原生字幕优先、仅编辑字幕 `text`、通过公开接口接入上游结果等个人工作流约束。
- 文档调整后检查链接、路径、YAML 头和示例；涉及命令或流程时同步直接相关文档。
- 若处理建议需要代码改动，按对应 Skill 的规则运行相关测试与代码规范检查，不把功能新增混同于文档纠错。

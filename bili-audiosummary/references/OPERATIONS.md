# 恢复与重新总结

已有任务需要恢复产物、补充音频、更换转写、重新总结或切换总结语言时，读取对应章节。执行前遵循 [SKILL.md 的环境策略](../SKILL.md#环境)，并保持与 job 匹配的数据目录；正常准备、总结与交付流程见 [SKILL.md](../SKILL.md#默认流程)。

## 恢复本地产物

恢复缺失 prompt；原生字幕任务同时可从本地 JSON 恢复 Markdown：

```powershell
uv run --no-sync python -m scripts.continue_summary "<absolute-summary-job-path>"
```

单纯恢复不改写总结。外部转写的本地 Markdown 缺失时，重新导入 manifest。

## 补充音频并更换转写

已有字幕任务需要改用音频转写时，先补充准备音频：

```powershell
uv run --no-sync python -m scripts.prepare_audio "<absolute-summary-job-path>"
```

需要登录态时加 `--cookies "<absolute-cookie-file-path>"`，Cookie 配置见[错误处理](ERROR-HANDLING.md#http-412-与-cookie)。

- 准备音频会登记 `resources.audio`，保留当前输入、总结和完成状态；失败时不撤销当前总结。
- 读取更新后的 job，将音频交给 `audio-transcribe`，再按[默认流程](../SKILL.md#2-依据任务状态继续)导入其公共 manifest。
- 已有字幕任务会拒绝冲突的字幕语言或 `--skip-subtitles`；已有外部转写任务继续使用转写输入，字幕选项不再适用。

显式传入 `--transcription-manifest` 总会校验并重新导入输入，同一路径也不例外。替换转写后旧总结归档，任务回到 `prompt_ready`，需重新编写、校验和交付。

## 按新要求重新总结

用户改变关注点、篇幅或格式，而转写和总结语言保持不变时：

```powershell
uv run --no-sync python -m scripts.continue_summary "<absolute-summary-job-path>" --regenerate-summary
```

该操作复用当前转写、归档旧总结并回到 `prompt_ready`。随后按本次用户请求重新编写总结、校验和交付，无需重新下载或转写。尚无转写输入时，先完成转写和导入。

## 切换总结语言

切换总结语言，保留下载资源和 transcript：

```powershell
uv run --no-sync python -m scripts.continue_summary "<absolute-summary-job-path>" --summary-language ja
```

支持语言和默认值见 [SKILL.md](../SKILL.md#1-准备资源和任务)。切换实际总结语言会归档旧总结并回到 `prompt_ready`，随后按新语言重新编写、校验和交付。

## 保留与失败恢复

替换转写、切换实际总结语言或重新总结时，旧总结归档为 `.superseded-<id>.md`；旧总结不能作为当前产物交付。

中途失败时按[输入与总结替换失败](ERROR-HANDLING.md#输入与总结替换失败)恢复；保留备份，恢复并校验后再交付。恢复、换转写和换语言均不需要删除任务。

只有明确需要删除整个任务时才使用 `scripts.remove_summary_job`，它会删除下载资源和用户总结。内部职责与数据流见[架构](ARCHITECTURE.md)。

# 环境与命令

## 环境配置

从 Skill 目录使用 Python 3.12 和 `uv`。运行 `scripts/check_dependencies.bat` 检查环境；它只检查并写报告，不安装依赖。依赖缺失或合同包不满足声明时，运行 `scripts/setup/setup_windows.bat` 同步，再检查；相同错误持续时根据日志定位，不反复同步。权限、项目文件和解释器错误按[错误处理](ERROR-HANDLING.md#环境与依赖)诊断。

三个音频 Skill 需要来自同一仓库 checkout：本 Skill 通过 `../audio-transcribe/packages/audio-transcribe-contract` 的本地 editable source 安装合同包。单独分发时必须一并提供该包，或先发布并改用已发布依赖；不能仅复制本 Skill 目录后运行 setup。

setup 会安装 Python 3.12、同步 `.venv` 并检查公共合同包。需要转写时另按 `audio-transcribe` 的 Skill 配置其环境和模型。

数据目录优先级为 CLI `--data-dir`、`SUBTITLE_CREATOR_DATA_DIR`、Skill 根目录。日志和检查报告位于 `<data-dir>/.cache/logs/`，任务位于 `<data-dir>/results/<audio-sha256>/`。切换目录不查找或迁移旧任务；源码和 `.venv` 不随数据目录移动。

```powershell
$env:SUBTITLE_CREATOR_DATA_DIR = "D:\skill-data\subtitle-creator"
$env:UV_CACHE_DIR = "$env:SUBTITLE_CREATOR_DATA_DIR\.cache\uv"
```

直接调用 `uv run` 时先设置 `UV_CACHE_DIR`，因为 uv 早于 Python 参数解析启动；`.bat` 启动器自动解析数据目录并保留显式 `UV_CACHE_DIR`。

## 分段命令约定

命令见 [SKILL.md](../SKILL.md#2-查看并按需校正)。ID 必须为当前 transcript 中从 `0` 开始的整数，不支持范围；`show/reset` 支持 `all`。`--context N` 仅用于查看单个 ID，返回前后各最多 N 段，首尾自动截断。校正和查看使用原分段 ID，与 SRT 切分后的序号无关。

按时间查看使用 `show --start <seconds> --end <seconds>`，两项必须同时提供，接受有限非负小数，且起点小于终点。仅返回满足 `segment.start < end` 且 `segment.end > start` 的完整分段；不重新分段、裁剪文字或修改时间轴。超出音频范围时返回其中有交集的分段，无匹配时成功返回空数组。不能与 `--id` 或 `--context` 混用。

批量 edit 接收非空 JSON 数组，每项字段严格为 `id`、`text`；拒绝重复或不存在的 ID、额外字段和纯空白文本。文本原样保存，包括引号、空白和换行。允许部分项目与当前文本相同；全部无变化则不提交。`--edits-file` 与 `--id`、`--text` 互斥。

`show` 输出单行 JSON：

```json
{"target_id":3,"segments":[{"id":3,"start":3,"end":4,"text":"示例"}]}
```

查看全部或按时间查看时 `target_id` 为 `null`；正文只写 stdout，日志不包含查看或编辑正文。其他命令成功时仅输出结果路径。退出码 `0` 表示成功，失败返回 `1`，stderr 给出错误及可用日志路径。

## 生成参数

```powershell
uv run --no-sync python -m scripts.generate_srt "<absolute-job-path>" --target-chars 24 --target-seconds 6 --min-gap 0.2
```

| 参数 | 约束 | 首次默认值 |
| --- | --- | --- |
| `--target-chars` | 正整数，计数非空白 Unicode 码点，含标点 | `24` |
| `--target-seconds` | 有限正数，单位秒 | `6` |
| `--min-gap` | 有限正数，单位秒 | `0.2` |

后续生成时，未显式提供的参数复用上次记录值；只覆盖一个参数也会保留其他值。尾部倍数固定为 `1.1`，没有可调参数。阈值为软目标，切点不保证词语完整；分段、校正回退及取整规则见[字幕渲染](SUBTITLE-RENDERING.md)。生成后的打开与有效性检查也使用保存的参数，不以默认值重新解释字幕。

## 删除任务

只在明确删除任务或无法恢复任务身份、结构时使用：

```powershell
uv run --no-sync python -m scripts.remove_subtitle_job "<absolute-job-path>"
```

只接受当前数据目录下 `results/<audio-sha256>/subtitle_job.json` 的绝对路径，删除整个任务目录，包括校正、基准和字幕；需要保留时先备份。目标不存在时也成功。恢复从音频重新打开任务；普通校正、reset、更换转写或修复 SRT 无需删除。

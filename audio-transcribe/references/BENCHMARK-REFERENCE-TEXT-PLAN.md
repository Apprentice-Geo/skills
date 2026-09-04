# Benchmark 固定 Reference 评估计划

## 状态

- 状态：已实施
- 记录日期：2026-09-03
- 实施日期：2026-09-03
- 范围：仅修改 `audio-transcribe` 的 benchmark 数据准备、指标、报告、测试和直接相关文档，不修改生产转写合同与 pipeline。

## 实施记录

- 已加入八个相邻静态文本片段、严格 loader、Reference CER/WER、schema 3 报告、冻结样本集恢复规则、Markdown 摘要和相应测试。loader 还在 worker 前验证所选 WAV 为非空单声道 16 kHz/16-bit PCM，这是计划中“WAV 错误前置失败”的具体实现。
- 首次真实 smoke 发现旧的确定性 `benchmark/tmp/<run-id>` 路径可能复用另一报告遗留的 workspace；实现改为每次 worker 路径追加随机 nonce，并以全新报告重跑验证，保证计划要求的运行隔离。
- 隔离后的真实 smoke 使用 faster-whisper、中文 8 分钟、两种 mode、单 repetition：warmup 和正式 `project-slicing` 均报告 `cache: reused=0`，两个正式 run 均成功并写入 Reference CER，配对的 project run 写入 Mode difference；报告为 schema 3。维护者随后完成 schema 3 的完整矩阵，96 个正式 run 全部成功，无失败 attempt。
- 中英文 reference 已由不同制作任务完成，并由未参与制作的任务独立检查固定来源、片段连续性、截点定位、明显错漏和制作标记。中文正文与固定《呐喊》底本机械一致；英文正文与 1925 扫描转写机械一致；底本外口播和截点均有旧报告中 Qwen3-ASR 两种 mode 的一致定位证据。
- 自动化实现阶段的 Agent 无法播放或理解本地音频；此后维护者已完成人工听校，包括八个截点、各段片头/片尾口播，以及英文录音可能采用后期修订词的 `confusion/arresting`、`said`、`startlingly`、`restaurants` 和 `surplus flesh` 等位置。本计划要求的人工验收项已完成。
- 维护者在人工听校后手动移除了 `benchmark/references/manifest.json` 原有的 `method` 和 `assistance` 字段。Reference 制作与文本处理记录保留在 [`benchmark/README.md`](../benchmark/README.md)，不作为 manifest schema 或每份报告的固定字段重复保存。

## 背景

当前 benchmark 的 `output_comparison` 按相同 repetition 比较 `project-slicing` 与 `provider-native` 的输出。中文使用 CER，英文使用 WER，但分母是 `provider-native` 输出，因此该指标只能回答两种执行模式产生了多大差异，不能回答任一模式相对于录音真实内容的准确率。

本计划增加固定 reference text，使每个成功 run 都能相对于同一参考文本计算 CER 或 WER。现有模式间差异继续保留，用于定位切片、合并或 Provider 原生处理造成的变化。

这里的 reference 由公版底本、已有 Qwen3-ASR 输出和争议位置的人工定点听校制作，不做全量逐字人工听校，因此它是固定评估基准，不等同于经过人耳完整确认的录音 ground truth。报告只称为 `Reference CER/WER` 或“相对于 reference 的错误率”，不称为绝对准确率。

## 已确认决策

| 主题 | 决策 |
| --- | --- |
| 文本来源 | 使用 benchmark 已固定的 LibriVox 录音所对应的公版原著底本。中文采用 LibriVox 登记的[新语丝鲁迅作品页](https://www.xys.org/pages/luxun.html)，必要时以[维基文库《呐喊》固定版本](https://zh.wikisource.org/w/index.php?title=%E5%90%B6%E5%96%8A&oldid=2602489)补足可读取文本；英文采用 LibriVox 登记的 [1925 年版扫描件](https://archive.org/details/the-great-gatsby_202101/page/n1/mode/2up)。 |
| 版权 | 两部原著和对应录音按 LibriVox 及底本页面标记的公有领域材料处理；保留作者、作品、朗读者、底本 URL 和来源摘要，本计划不再重复进行版权审查，也不作“任何司法辖区均零风险”的保证。 |
| 校订方式 | 不进行全量逐字人工听校。使用已有 benchmark 报告中 Qwen3-ASR 的 `project-slicing` 与 `provider-native` 去重输出辅助定位；repetition 不视为独立校订证据。底本外口播、影响 reference 的底本/朗读冲突和四个截点必须人工定点听校。 |
| reference 定位 | 原著底本是正文内容的第一来源；Qwen3 输出用于识别录音片头、书名和章节名等底本外口播，定位四个样本的结束位置，并提示朗读增删或版本差异。 |
| 制作方式 | Reference 是一次制作、提交后长期读取的静态 benchmark 数据；不实现或保留 `prepare_references` 生成脚本。制作时可以使用临时命令或人工工具，但不把一次性对齐流程变成项目功能。 |
| 中文比较 | Reference 保留所依据底本的可读字形。计算指标时对 hypothesis 和 reference 对称执行 NFKC、OpenCC `t2s`，再移除 Unicode 空白与标点，因此繁简体差异本身不计为 CER。 |
| 生产边界 | reference 只属于 benchmark，不进入 `result_manifest.json`、转写 cache、`variant_id` 或其他公开 artifact。 |

## 目标与非目标

### 目标

- 为中英文 8、16、32、64 分钟样本提供可提交、可校验且与音频 SHA256 绑定的 reference text。
- 对每个成功 run 计算相对于 reference 的中文 CER 或英文 WER。
- 在 JSON 和 Markdown 报告中同时展示 Reference CER/WER 与现有模式间差异。
- Reference 或评估合同变化后生成全新的完整 benchmark 报告，不迁移或重评分旧报告。
- 在运行选定样本的模型前发现其 reference 缺失、音频不匹配或数据损坏。

### 非目标

- 不建立通用有声书对齐或人工标注平台。
- 不保留只使用一次的 reference 下载、抽取或自动对齐脚本。
- 不为 reference 提供词级时间戳精度保证。
- 不将 Qwen3 输出本身直接宣布为真实文本。
- 不把未经全量逐字人工听校的 Reference CER/WER 描述为绝对准确率。
- 不删除性能、RTF、资源占用、标点数量或现有模式差异指标。
- 不修改历史 `references/BENCHMARK-2026-08-22.md` 中已经记录的实验数值。

## Reference 数据设计

新增提交到仓库的 `benchmark/references/` 目录。正文按相邻样本区间拆成纯文本片段，既避免四份嵌套 reference 重复保存，也避免在正文编辑后手工维护字符 offset：

```text
benchmark/references/
├── manifest.json
├── zh/
│   ├── 000-008.txt
│   ├── 008-016.txt
│   ├── 016-032.txt
│   └── 032-064.txt
└── en/
    ├── 000-008.txt
    ├── 008-016.txt
    ├── 016-032.txt
    └── 032-064.txt
```

`manifest.json` 保存运行身份、片段顺序和必要的来源追溯信息。schema 形状如下：

```json
{
  "schema_version": 1,
  "languages": {
    "zh": {
      "source": {
        "work": "呐喊 (Call to Arms)",
        "author": "鲁迅",
        "reader": "Jing Li",
        "audio_url": "...",
        "text_url": "..."
      },
      "parts": [
        {
          "through_minutes": 8,
          "path": "zh/000-008.txt",
          "sha256": "...",
          "sample_audio_sha256": "..."
        }
      ]
    }
  }
}
```

具体约束：

- 每个片段保存指标规范化之前、与录音范围对齐的固定底本文本，并包含经过人工定点听校确认的片头、作品名和章节名等底本外口播。未做全量听校，因此不保证正文逐字反映朗读者的所有增删、口误或版本差异。
- manifest 顶层及嵌套对象拒绝缺失字段、错误类型和未知字段；`languages` 必须恰为 `zh`、`en`，每种语言的 `source` 必须包含非空的 `work`、`author`、`reader`、`audio_url` 和 `text_url` 字符串。
- `through_minutes` 必须精确依次为 `[8, 16, 32, 64]`。某个样本的 reference 是从首片段到对应 `through_minutes` 片段的拼接结果。
- 每个片段记录自身文件 SHA256；`sample_audio_sha256` 记录对应累积 WAV 文件字节的 SHA256，并与 `benchmark/data/samples.json` 及实际所选 WAV 一致。所有摘要必须是 64 位小写十六进制。
- `path` 相对于 `benchmark/references/` 解析；解析后的普通文件必须仍在该目录内且不得被重复引用。这里不建立通用文件系统安全层，只复用项目现有的路径包含检查方式。
- `manifest.json` 和片段均使用 UTF-8、无 BOM、LF 换行并禁止 NUL；通过 `.gitattributes` 固定换行。文件 SHA256 和 manifest SHA256 均针对工作树中的原始文件字节计算，规范化 unit digest 则以 NUL 连接后编码为 UTF-8 再计算。
- reference 数据保留底本字形、原始大小写和标点。评分时 hypothesis 与 reference 使用同一套规范化：中文为 NFKC、OpenCC `t2s` 后删除 Unicode 空白和标点；英文为 NFKC、`casefold` 后提取 Unicode 单词。
- 中文 CER 比较的是双方的简体规范化字符序列。报告中的 `reference_sha256` 是该规范化 unit 序列以 NUL 连接后的 SHA256；片段文件 SHA256 则校验未经规范化的静态来源文本，两者含义不得混用。
- benchmark 只读取这些文件，不在运行期间创建、修复或改写 reference。

## Reference 制作流程

Reference 由维护者一次性制作并作为普通静态数据提交，不新增项目入口或永久生成器。建议流程如下：

1. 校验 `benchmark/sources.json`、`benchmark/data/samples.json`、八个 WAV 文件及其 SHA256。
2. 从 LibriVox 登记的底本中人工抽取最长样本涉及的正文，记录作品、作者、朗读者、音频 URL、文本 URL 和固定页面版本；不提交整部书。
3. 从 `benchmark/reports/2026-08-22.json` 提取 Qwen3-ASR 两种 mode 的成功文本，先按文本内容去重，再作为正文定位、底本外口播和朗读差异的提示。现有报告中同一 mode 的三次 repetition 文本逐字相同，因此 repetition 不视为独立校订证据，也不要求实现“多数投票”。
4. 正文默认采用固定底本；对 Qwen3 提示的朗读增删、口误或版本差异，只在定点听校确认后修改 reference。所有底本外口播也必须经定点听校确认；两个 mode 的一致只用于缩小听校范围，不能替代人耳确认。
5. 利用四个累积样本的输出初步定位 8、16、32、64 分钟结束位置，再逐一听取每个截点附近的音频，确认最后一个完整口语单位后把最长正文拆为四个相邻片段。无法唯一确认时停止该语言的制作，不猜测文本。
6. 通过将来实现的 reference loader 运行一次完整校验，确认片段摘要、四个音频摘要、顺序、路径和规范化后非空。只提交最终片段与 `manifest.json`；临时下载、抽取、对齐命令和中间校订文件不进入仓库。

Qwen3 只承担导航和差异提示职责，不因两个 mode 使用同一模型或重复运行结果一致而提升为 ground truth。后续若扩大人工听校范围或修改 reference，应更新受影响片段摘要、报告中的 reference identity，以及 `benchmark/README.md` 的校订记录，而不是无痕覆盖。

## Benchmark 指标与报告改动

### JSON 报告

将报告 schema 从 2 提升到 3，并增加报告级 `reference_set`，记录 reference schema、manifest SHA256，以及 `runs` 中出现但不含 warmup 的每个样本的音频 SHA256 和规范化 reference SHA256。每个新 schema 3 run 也直接记录自身 `audio_sha256`，用于审计实验输入。

每个成功 run 新增 `reference_comparison`：

```json
{
  "metric": "cer",
  "hypothesis_units": 1000,
  "reference_units": 1010,
  "hypothesis_sha256": "...",
  "reference_sha256": "...",
  "hypothesis_punctuation": 120,
  "reference_punctuation": 125,
  "edit_distance": 20,
  "error_rate": 0.0198019802
}
```

计算规则：

- 编辑距离的左侧是 run 输出，右侧是 reference，错误率分母固定为 `reference_units`。
- hypothesis 与 reference 必须对称使用报告 `comparison_policy` 中记录的规则。中文两侧均先转换为简体；不得只把 Reference 预先简体化而保留 hypothesis 的繁体字形。
- reference 规范化后为空属于数据错误，benchmark 必须停止。
- `output_comparison` 保持现有字段与算法，继续只写入配对的 `project-slicing` run，避免把模式差异与绝对准确率混为一谈。
- reference 不影响推理输入，因此不加入 `run_id`，但属于报告身份。正常恢复 schema 3 报告时，若已记录的 reference identity 与当前数据不同，应在任何模型运行前停止；维护者必须指定新的报告路径并重新运行完整 benchmark，不能复用旧 run 或静默改写旧报告。
- schema 3 报告使用固定的 benchmark comparison policy id；英文 tokenizer 明确保留单词内部的 `'` 或 `’`。schema 2 及其他旧 schema 报告不迁移，读取时继续明确失败。
- schema 3 断点续跑沿用现有 `run_id`：已成功的 run 跳过，失败 attempt 保留并在下次执行时追加重试；同一报告中的 reference identity、comparison policy 或 run identity 不允许变化。

### Markdown 报告

- 每个 mode 行增加中位 `Reference CER` 或 `Reference WER`。
- 保留 `project-slicing` 行上的模式间 Difference，并将标题明确为 `Mode difference`。
- 标点列以 `hypothesis/reference` 显示当前 mode 与 reference 的数量；原有 project/native 标点比较可留在 JSON 中，不再挤入同一个 Markdown 单元格。
- 报告开头展示 reference manifest digest，不重复写入固定的 method、assistance 或人工听校说明；Reference 来源和校订过程由 `benchmark/README.md` 统一记录。
- 当某个成功 run 缺少有效 reference comparison 时，摘要生成应失败，不输出看似完整的表格。

## 实现步骤

1. 在 `benchmark/` 下实现静态 reference schema、只读加载、路径约束、digest 和音频身份校验。
2. 按一次性人工流程制作并提交 `manifest.json` 与八个增量文本片段，不实现 `benchmark.prepare_references`。
3. 将现有文本规范化与编辑距离逻辑整理为同时支持 hypothesis/reference 和 project/native 的公共 benchmark 内部函数；显式测试中文双方都经过 OpenCC `t2s`，不改变生产文本规范化行为。
4. 在 `scripts.benchmark` 启动选定矩阵前验证 manifest 全局结构与全部纯 metadata，只打开和校验所选样本需要的片段及所选 WAV；恢复报告时还要验证所有既有 run 的 reference identity。成功 run 完成后记录 `audio_sha256` 和 `reference_comparison`。
5. 将每个 Provider 的 warmup 从固定 `zh-8min.wav` 改为该 Provider 矩阵中的第一个已选样本，避免未选择的本地 WAV 阻塞筛选运行。
6. 将新报告升级为 schema 3；保留按 `run_id` 跳过成功 run、保留失败并追加 attempt 的断点续跑行为，在 reference identity、comparison policy 或报告结构不匹配时于推理前停止。
7. 更新 Markdown 表格和 `benchmark/README.md`，记录静态 Reference 来源、一次性制作流程、简体化规则、Qwen3-assisted 限制、schema 3、断点续跑和两类指标的不同含义。

## 验证责任

Agent 完成实现后运行自动化测试和代码检查，并只使用最短的 8 分钟音频做一次真实 smoke test。建议覆盖核心依赖中的 faster-whisper、中文 CER 和两种 mode，各运行一次：

```powershell
uv run --no-sync python -m scripts.benchmark --provider faster-whisper --language zh --minutes 8 --mode project-slicing --mode provider-native --repetitions 1 --report benchmark/reports/agent-smoke-<YYYY-MM-DD>.json
```

该 smoke report 只验证真实音频、两条执行路径、Reference CER 和报告写入，不作为正式 benchmark 结果，也不要求 Agent 运行完整矩阵。

开发完成并交付后，由维护者运行完整 benchmark。为保证跨日期中断后仍写入同一报告，应显式固定新报告路径；中断后重复同一命令即可断点续跑：

```powershell
uv run --no-sync python -m scripts.benchmark --report benchmark/reports/<YYYY-MM-DD>.json
```

完整运行结束后，维护者检查 96 个正式 run 均成功，将生成的结果整理为新的、可提交的 `references/BENCHMARK-<YYYY-MM-DD>.md`，并保留 `references/BENCHMARK-2026-08-22.md` 不变。被忽略的 `benchmark/reports/` 保存本地原始 JSON 和 Markdown。

## 测试与验证

在 `tests/test_benchmark.py` 及必要的 benchmark 专用测试中覆盖：

- 中文和英文 hypothesis/reference 的 CER/WER 分母、摘要和 digest；至少包含繁体 hypothesis 对简体 reference、简体 hypothesis 对繁体 reference 均为零差异的用例。
- reference manifest 缺失、字段或摘要格式错误、片段 digest 不匹配、音频 SHA256 不匹配、空 reference、NUL、路径逃逸、重复片段，或 `through_minutes` 不精确等于 `[8, 16, 32, 64]` 时，在 worker 启动前失败。
- 八个固定样本都能解析出非空 reference，短样本文本是长样本文本的前缀。
- manifest 的 shape 与纯 metadata 始终全局验证；筛选运行只打开并验证所选样本及其前置文本片段和所选 WAV。未选择的本地 WAV 缺失不得阻塞单样本 benchmark，warmup 必须使用第一个已选样本。
- schema 3 报告写入和 reference identity 相同的断点续跑：成功 run 跳过，失败 attempt 保留并重试；reference identity、comparison policy 或报告结构变化时必须在推理前失败。
- schema 2 及其他旧 schema 报告不迁移，读取时明确失败并要求使用新的报告路径完整重跑。
- `output_comparison` 的现有配对和中位数行为不回归。
- Markdown 同时显示 reference 错误率和 mode difference，不重复 `benchmark/README.md` 中的 Reference 制作与校订说明。
- 自动化验证之外，Agent 只运行一次 8 分钟、单 repetition 的真实 smoke test；完整 96-run benchmark 明确由维护者在开发完成后执行。

聚焦验证命令：

```powershell
uv run pytest tests/test_benchmark.py
uv run ruff check
uv run pyright
uv run ruff format --check .
git diff --check
```

Reference 已完成真实数据检查和本计划要求的人工听校，静态文件绑定当前 `samples.json` 的八个摘要。当前校订范围和制作过程以 `benchmark/README.md` 为准；除非另有明确记录，不把 Reference CER/WER 描述为绝对准确率。

## 验收标准

- 仓库包含八个增量 reference 文本片段和一个 manifest，覆盖现有八个 benchmark WAV 摘要。
- benchmark 在模型运行前拒绝缺失、损坏或音频身份不一致的 reference。
- 每个成功 run 都有以 reference 为分母的 CER 或 WER；两种 mode 均可独立比较。
- 中文 hypothesis 与 reference 均经 NFKC 和 OpenCC `t2s` 后再计算 CER，繁简字形差异不单独计错。
- 现有 `output_comparison` 继续可用，报告不会把它描述成相对于真实文本的准确率。
- 报告记录 reference manifest digest；reference 来源、Qwen3 辅助定位和已完成的人工听校记录保留在 `benchmark/README.md`，不要求 manifest 或报告保存 `method`、`assistance` 字段。
- Agent 已通过自动化检查和最短 8 分钟音频的真实 smoke test，不要求其运行完整矩阵。
- 维护者使用新的固定报告路径运行完整 benchmark；命令中断后可跳过已成功 run 并重试失败 run，最终得到 96 个成功正式 run。
- 维护者新增一份以完整运行日期命名、可提交的 `references/BENCHMARK-<YYYY-MM-DD>.md`，并保留 2026-08-22 历史记录。
- `benchmark/README.md`、测试和报告 schema 与实现一致，生产 artifact 合同没有变化。

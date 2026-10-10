# AI Karaoke Subtitles

AI Karaoke Subtitles 从歌曲音频和 LRC 歌词生成可审核、可编辑的卡拉 OK `ASS` 字幕。项目重点面向日语歌曲，在 6GB 显存设备上也能运行完整链路。

```text
原曲 + LRC
  → Mel-Band RoFormer 分离人声/伴奏
  → LRC 清洗、显式注音与和声结构识别
  → MMS Karaoke CTC 逐音节强制对齐
  → Whisper + kanjiconv 核对日语读音
  → K1/K2 交错字幕、注音与 KTV 倒计时
```

生产链路中的分离和对齐模型已经固定；日常使用只需要选择 Whisper 日语读音检查的模型大小。

## 主要能力

- 生成逐音节 `\kf` 时间轴，保留原歌词的空格、标点和显示文字。
- 把 LRC 时间戳当作搜索提示，不用原时间戳强行覆盖模型结果。
- 识别 LRC 元数据、翻译、显式注音和括号和声。
- 独立对齐括号和声，按实际时间重叠度决定保留或移除。
- 按“LRC 显式读音 > 可靠的 Whisper 纠音 > kanjiconv 词典读音”生成日语注音。
- 为新歌词序列增加与实际提前量一致的 0～3 个一秒倒计时点。
- 输出对齐报告和注音报告，方便定位低置信度、缺字或读音分歧。
- CLI 和 Web GUI 均支持串行批处理；单首失败不会中止整个批次。
- Web GUI 支持原曲同步试听、注音预览、ASS 编辑保存和批量下载。

## 环境要求

- Windows 10/11 或 Linux 64 位系统。
- Python 3.10。
- FFmpeg。
- 安装 `ctc-forced-aligner` 时需要 Git。
- NVIDIA GPU 可显著提速；默认参数按 6GB 显存设计。没有 NVIDIA GPU 也可以使用 CPU，但处理时间会明显增加。
- 首次处理会下载模型，请保持联网并预留数 GB 缓存空间。

## Windows 一键安装

Windows 用户无需预先安装 Python、Conda、Git 或 FFmpeg：

1. 下载并解压完整项目目录。
2. 双击 `windows_online_installer/install_online.bat`。
3. 安装完成后双击 `windows_online_installer/run_installed_gui.bat`。
4. 浏览器会自动打开 `http://127.0.0.1:7860`。

安装器会在 `%LOCALAPPDATA%\AIKaraokeWebGUIOnline` 创建隔离环境，自动检测 NVIDIA 显卡、安装 PyTorch 和项目依赖，并执行编译与导入自检。重复运行安装器会更新应用并复用已有缓存。

卸载时双击 `windows_online_installer/uninstall_online.bat`。详细行为见 [Windows 安装器说明](windows_online_installer/README.md)。

## Conda 手动安装

### 1. 创建环境和系统工具

```bash
conda create -n ai_karaoke python=3.10 -y
conda activate ai_karaoke
conda install -c conda-forge ffmpeg git -y
```

### 2. 安装 PyTorch 运行时

`torch`、`torchaudio` 和 `torchcodec` 必须来自相互兼容的平台版本。请优先使用 [PyTorch 官方安装选择器](https://pytorch.org/get-started/locally/) 给出的命令，并在同一个索引中加入 `torchcodec`。

CPU 示例：

```bash
pip install torch torchaudio torchcodec --index-url https://download.pytorch.org/whl/cpu
```

NVIDIA 示例（索引版本需要与当前 PyTorch 官方支持情况和显卡驱动匹配）：

```bash
pip install torch torchaudio torchcodec --index-url https://download.pytorch.org/whl/cu129
```

### 3. 安装项目依赖

```bash
pip install -r requirements.txt
```

`requirements.txt` 只维护代码直接使用的运行依赖；`numpy`、`scipy`、`PyYAML`、`ml-collections` 等由上游库按需要安装，不再重复声明。

### 4. 安装后检查

```bash
python -c "import torch, stable_whisper, ctc_forced_aligner, mel_band_roformer, kanjiconv; print('CUDA:', torch.cuda.is_available())"
python main.py --help
```

## 准备歌曲和歌词

推荐把音频与同名 LRC 放在同一目录：

```text
music/
├── Grand Symphony.flac
└── Grand Symphony.lrc
```

最小 LRC 示例：

```text
[00:12.30]真っ新な扉開くなら
[00:17.80]いつだって君と一緒がいい
```

解析规则：

- `[ar:]`、`[ti:]` 等元数据会被忽略。
- LRC 时间戳只用于缩小音频搜索范围，最终时间来自 CTC 对齐。
- 汉字后紧跟的纯假名括号可作为显式注音，例如 `未来(きょう)`。
- 英文括号或完整的日语旁唱会先作为和声候选保留，再通过音频时间轴判断是否与主歌高度重叠。
- 双语翻译会按语言和文字特征过滤；建议仍尽量提供结构清楚的原文 LRC。

## CLI 使用

### 单曲

同名 LRC 自动发现：

```bash
python main.py -i /music/song.flac -o /output --lang ja
```

手动指定 LRC：

```bash
python main.py -i /music/song.flac -l /lyrics/song.lrc -o /output --lang ja
```

### 批量处理

一次传入多首歌曲：

```bash
python main.py \
  -i /music/song-a.flac /music/song-b.flac \
  -o /output \
  --lang ja
```

扫描一个目录的当前层级：

```bash
python main.py --input-dir /music/album -o /output --lang ja
```

批量模式会为每首音频寻找同目录、同名的 `.lrc` 或 `.LRC`。`--lyric` 只适用于单曲任务。

### 常用参数

| 参数 | 作用 | 默认值 |
| --- | --- | --- |
| `-i, --input` | 一首或多首音频路径，可重复传入 | 无 |
| `--input-dir` | 扫描目录当前层级的常见音频文件 | 无 |
| `-l, --lyric` | 单曲 LRC；省略时自动寻找同名文件 | 自动发现 |
| `-o, --output` | 输出根目录 | 必填 |
| `--lang` | 歌词语言，如 `ja`、`en` | `ja` |
| `--ctc-batch-size` | CTC emission batch；6GB 显存建议保持 1 | `1` |
| `--ctc-number-reading` | 阿拉伯数字按 `auto`、`ja` 或 `en` 对齐 | `auto` |
| `--furigana-whisper-model` | 日语读音检查模型大小 | `large-v3` |
| `--no-whisper-readings` | 关闭 Whisper 日语读音检查 | 关闭该选项 |
| `--kara-advance-ms` | 新歌词序列提前显示时长 | `3000` |
| `--kara-sep-threshold-ms` | 判断新歌词序列的间隔阈值 | `10000` |
| `--kara-primary-color` | 未唱歌词颜色 | `&H00FFFFFF` |
| `--kara-accent-color` | 已唱歌词颜色 | `&H002F1CD8` |
| `--verbose` | 显示第三方库诊断输出 | 关闭 |

日语读音检查可选 `tiny`、`base`、`small`、`medium`、`large-v3`。显存紧张时先降低该模型大小；分离和 CTC 对齐模型保持固定。

## Web GUI 使用

```bash
python web_gui.py --host 127.0.0.1 --port 7860
```

打开 `http://127.0.0.1:7860` 后可以：

- 选择多首歌曲或扫描目录，查看批次和单曲阶段进度。
- 调整字幕配色；低频参数集中在“高级设置”。
- 每首歌完成后独立试听原曲，并按 ASS 时间轴预览 K1/K2 扫色和注音。
- 点击歌词跳转播放位置，直接编辑并保存 ASS。
- 下载单首 ASS、对齐报告、注音报告，或打包下载整个批次的 ASS / 音频与 ASS。

GUI 只绑定本机地址，文件路径、音频和字幕不会上传到外部服务。模型下载仍由各模型仓库完成。

## 输出文件

```text
{output}/{song_name}/
├── {song_name}_vocal.wav
├── {song_name}_accomp.wav
├── {song_name}_alignment_report.json
├── {song_name}_pronunciation_report.json  # 日文且启用 Whisper 检查时
└── {song_name}.ass
```

- `*_alignment_report.json`：记录 LRC 漂移、重叠、零时长 token、CTC 置信度和和声取舍。
- `*_pronunciation_report.json`：记录词典读音、听到的读音、最终读音、显式注音和纠音理由。
- `*.ass`：最终 K1/K2 交错卡拉 OK 字幕，可在 GUI 或 Aegisub 中继续修改。

## 项目结构

```text
ai_karaoke/
├── main.py                         # CLI 启动入口
├── web_gui.py                      # Web GUI 启动入口
├── requirements.txt                # 非平台相关运行依赖
├── ai_karaoke/
│   ├── application/
│   │   ├── pipeline.py             # 批任务、配置、产物契约与总流程
│   │   └── generator.py            # 对齐报告与基础 ASS 生成
│   ├── audio/separation.py         # Mel-Band RoFormer 分离阶段
│   ├── lyrics/parser.py            # LRC 清洗、注音和和声结构
│   ├── alignment/
│   │   ├── aligner.py              # MMS 模型载入与对齐总编排
│   │   ├── core.py                 # CTC 路径与 emission 对齐
│   │   ├── harmony.py              # 和声独立对齐与时间重叠判定
│   │   ├── models.py               # 对齐数据结构与时间辅助函数
│   │   ├── text.py                 # 假名、数字与罗马字对齐单元
│   │   └── windows.py              # 分块窗口、漂移和重叠恢复
│   ├── pronunciation/
│   │   ├── reading.py              # kanjiconv 上下文读音
│   │   ├── evidence.py             # Whisper 证据选择与纠音提案
│   │   ├── whisper.py              # 整曲听写、局部重试与报告
│   │   ├── ruby.py                 # ASS 注音合并与写入
│   │   └── cli.py                  # 独立注音工具入口
│   ├── subtitles/
│   │   ├── style.py                # K1/K2 样式编排
│   │   ├── karaoke_tags.py         # k 标签修复与 KTV 倒计时
│   │   ├── colors.py               # ASS 颜色默认值与校验
│   │   ├── document.py             # ASS 时间与区段辅助函数
│   │   ├── templates.py            # K 样式和模板注释
│   │   └── cli.py                  # 独立样式工具入口
│   ├── interfaces/
│   │   ├── cli.py                  # CLI 参数与批量执行
│   │   └── console.py              # 统一终端进度输出
│   └── web/
│       ├── server.py               # Web 服务启动入口
│       ├── handler.py              # HTTP 路由和文件传输
│       ├── forms.py                # 页面模板、文件选择和表单解析
│       ├── jobs.py                 # 批任务状态与串行 worker
│       ├── artifacts.py            # ASS 原子保存
│       └── static/                 # 按表单、进度、审核拆分的前端资源
├── tests/                          # 按歌词、对齐、读音、字幕、Web 拆分的测试
├── windows_online_installer/       # Windows 安装、运行与卸载脚本
├── PIPELINE.md                     # 精度策略和内部数据契约
└── TODO.md                         # 保留的后续工作记录
```

## 测试与开发检查

进入项目目录并激活环境后运行：

```bash
python -m unittest discover -s tests -v
node --test tests/test_ass_preview.mjs
for file in ai_karaoke/web/static/*.js; do node --check "$file"; done
ruff check --select E9,F,I ai_karaoke main.py web_gui.py tests
python -m compileall -q ai_karaoke main.py web_gui.py tests
python -m ai_karaoke.pronunciation.cli --self-test
python -m ai_karaoke.subtitles.cli --self-test
```

项目使用 Python 标准库 `unittest`，不需要额外测试框架。提交结构或行为改动前，应运行以上检查。

## 常见问题

### `torch.cuda.is_available()` 返回 `False`

通常是 PyTorch wheel 与显卡驱动不匹配。重新按 PyTorch 官方安装选择器安装 `torch`、`torchaudio` 和 `torchcodec`，不要混用 CPU 与 CUDA 索引。

### 首次处理很慢或长时间没有进入推理

固定链路会首次下载 Mel-Band RoFormer、MMS CTC 和 Whisper 模型。检查网络、磁盘空间以及 Hugging Face 缓存目录是否可写。

### 6GB 显存不足

保持 `--ctc-batch-size 1`，把 `--furigana-whisper-model` 从 `large-v3` 降到 `medium`、`small` 或更小。批处理本身按顺序执行，不会并行占用多份模型显存。

### `ctc-forced-aligner` 安装失败

确认 Git 可用，并重新执行 `pip install -r requirements.txt`。Windows 一键安装器会自动安装 Git。

### 字幕时间或读音仍有疑问

先查看对应的 `*_alignment_report.json` 和 `*_pronunciation_report.json`。报告保留了模型证据与最终取舍，比只观察 ASS 更容易判断问题来自音频分离、对齐还是读音检查。

# AI Karaoke Subtitles

一个从音频与 LRC 逐行歌词自动生成卡拉 OK 字幕（`.ass`）的工具链。

它会完成以下流程：

1. 音频分离（Demucs 或官方伴奏相消）
2. 按 LRC 时间戳进行受限对齐（Whisper + stable-ts）
3. 为日文歌词注入注音（furigana）
4. 应用卡拉 OK 分层样式与渐变模板

最终输出可直接用于 Aegisub / VSFilter 系字幕渲染流程。

## 功能特性

- 支持 `LRC` 自动解析，过滤作词/作曲等非歌词信息
- 支持双语交错歌词中按语言自动选主歌词行
- 对齐结果写入 `\kf` 中，实现自动打 k 轴，并对标点与空白做安全处理
- 支持自定义卡拉 OK 主色和强调色

## 项目结构

- `main.py`：总入口（推荐）
- `separator.py`：人声/伴奏分离
- `aligner.py`：LRC 解析 + Whisper 对齐 + 生成 ASS
- `furigana.py`：日文注音注入
- `kara_style.py`：分层样式与模板注释注入

## 环境要求

- Python 3.10+
- 系统依赖（`requirements.txt` 之外）：
  - `ffmpeg`（读取/解码 mp3/flac 等常见音频格式）
  - `demucs` 命令行（无官方伴奏时用于人声分离）

## 安装步骤

下面以 Conda 环境为例。

### 1) 创建并激活环境

```bash
conda create -n ai_karaoke python=3.10 -y
conda activate ai_karaoke
```

### 2) 安装系统依赖（ffmpeg）

```bash
sudo apt update
sudo apt install -y ffmpeg
```

### 3) 安装 PyTorch / Torchaudio（按你的 GPU/CUDA 选择）

`torch` 与 `torchaudio` 必须匹配你的运行环境（CPU / CUDA 版本）。

- 先到 PyTorch 官方安装页选择命令：<https://pytorch.org/get-started/locally/>
- 选择后执行你自己的命令（示例）：

```bash
# 示例：CPU
pip install torch torchaudio

# 示例：NVIDIA GPU（请将 cu121 替换为你机器匹配的 CUDA 版本）
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121

# 示例：AMD GPU（请将 rocm6.2 替换为你机器匹配的 ROCm 版本）
pip install torch torchaudio --index-url https://download.pytorch.org/whl/rocm6.2
```

验证显卡是否被成功识别：

```bash
python -c "import torch; print('GPU 可用:', torch.cuda.is_available())"
```

### 4) 安装其余 Python 依赖

```bash
pip install stable-ts demucs librosa numpy scipy soundfile pykakasi
```

依赖说明：

- `stable-ts`：Whisper 对齐（代码中通过 `stable_whisper` 使用）
- `demucs`：无人声伴奏时做人声分离（提供 `demucs` 命令）
- `librosa` / `numpy` / `scipy` / `soundfile`：音频读取与相消处理
- `pykakasi`：日文注音转换

### 5) 安装后快速检查

```bash
python -c "import torch, torchaudio, stable_whisper, librosa, numpy, scipy, soundfile, pykakasi; print('python deps ok')"
demucs --help
ffmpeg -version
```

## 快速开始

```bash
python main.py -i /path/to/song.mp3 -l /path/to/song.lrc -o /path/to/output --lang ja
```

### 不传 `--lyric`（自动寻找同名 LRC）

当音频同级目录下有同名 `.lrc` 文件时可省略：

```bash
python main.py -i /path/to/song.mp3 -o /path/to/output --lang ja
```

### 传入官方伴奏（跳过 Demucs）

```bash
python main.py \
  -i /path/to/song.mp3 \
  -l /path/to/song.lrc \
  -o /path/to/output \
  --inst /path/to/official_inst.wav \
  --lang ja
```

## 常用参数说明（`main.py`）

```text
-i, --input                  输入歌曲路径（必填）
-l, --lyric                  LRC 路径（可选；缺省时自动查找同名 .lrc）
-o, --output                 输出目录（必填）
--inst                       官方伴奏路径（可选）
--lang                       歌曲语言，如 ja/zh/en
--whisper-model              Whisper 模型：tiny/base/small/medium/large/turbo
--kara-advance-ms            卡拉 OK 提前量（ms）
--kara-sep-threshold-ms      卡拉 OK 序列切分阈值（ms）
--kara-primary-color         主色（ASS 颜色或名称）
--kara-accent-color          强调色（ASS 颜色或名称）
```

Whisper 模型显存参考（FP16，近似值）：

- `tiny/base`：约 1GB
- `small`：约 2GB
- `medium`：约 5GB
- `large`：约 10GB
- `turbo`：约 6GB

## 输出结果

执行后会在输出目录生成：

```text
{output}/{song_name}/
├── {song_name}_vocal.wav
├── {song_name}_accomp.wav
└── {song_name}.ass
```

其中 `{song_name}.ass` 为最终卡拉 OK 字幕文件。

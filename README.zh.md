# Rimed Voice Typing for Linux

基于 [voice-typing-linux](https://github.com/GitJuhb/voice-typing-linux)，增加了中文输入（Rime 内嵌）、中文流式识别模型和 Fedora/systemd 支持。

## 功能

- **IBus 输入法引擎** — 通过 `commit_text` 原子插入文字，无按键模拟延迟，终端、浏览器、编辑器全部支持
- **双通道流式识别** — sherpa-onnx 实时出字（~100ms），faster-whisper turbo 修正精度，文字即说即现
- **中文输入（Rime）** — IBus 引擎内嵌 librime，Shift 切换中文/英文模式，双拼候选词内联显示，无需运行第二个输入法
- **中文流式模型** — 支持 zipformer-zh（中英双语，~488MB）
- **GPU 加速** — TF32、cudnn benchmark、显存预分配、模型预热，修正延迟约 0.1-0.2s
- **预录缓冲** — 600ms 环形缓冲，不丢第一个字
- **语音指令** — 窗口管理、文字编辑、应用启动、网页搜索
- **按键通话（PTT）** — 按住或切换模式，可配置热键
- **Fedora/systemd 支持** — 无需 Nix，直接用 venv 启动

## 快速开始
```bash
git clone https://github.com/你的用户名/rimed-voice-typing-linux.git
cd rimed-voice-typing-linux

python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r requirements.txt

./voice --streaming --device cuda
```

### NixOS
```bash
nix-shell
./voice --streaming --device cuda
```

## IBus 配置

### 1. 安装组件描述文件
```bash
mkdir -p ~/.local/share/ibus/component
cp voice-typing-ibus.xml ~/.local/share/ibus/component/
```

编辑 `<exec>` 路径：
```bash
nano ~/.local/share/ibus/component/voice-typing-ibus.xml
```

**非 NixOS：**
```xml
<exec>/home/你的用户名/rimed-voice-typing-linux/.venv/bin/python /home/你的用户名/rimed-voice-typing-linux/ibus_voice_engine.py</exec>
```

**NixOS：**
```xml
<exec>/home/你的用户名/rimed-voice-typing-linux/ibus-engine-voice-typing</exec>
```

用 `pwd` 确认实际路径。

### 2. 重启 IBus 并添加引擎
```bash
ibus restart
ibus engine voice-typing
```

或在 GNOME 设置 → 键盘 → 输入源 里添加"Voice Typing"。

### 3. 启动
```bash
# 终端 1：IBus 引擎（如果不用 ./voice 脚本单独启动的话）
python ibus_voice_engine.py

# 终端 2：语音识别主进程
./voice --streaming --device cuda
```

`./voice` 脚本会自动管理 IBus 引擎子进程，通常只需运行一条命令。

## 中文输入（Rime）

安装 librime 后自动启用。按 **Shift** 在英文/语音模式和中文模式之间切换，候选词显示在 preedit 区域。

### 更换输入方案

默认使用 `double_pinyin_mspy`（微软双拼）。通过环境变量更换：
```bash
export RIME_SCHEMA=double_pinyin    # 自然码
export RIME_SCHEMA=luna_pinyin      # 全拼
export RIME_SCHEMA=wubi86           # 五笔
./voice --streaming
```

方案必须已安装在 Rime 用户目录中。使用 fcitx-rime 或自定义路径：
```bash
export RIME_USER_DIR=~/.config/fcitx/rime
export RIME_SCHEMA=double_pinyin
./voice --streaming
```

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `RIME_SCHEMA` | `double_pinyin_mspy` | 方案 ID，需与 `.schema.yaml` 文件名一致 |
| `RIME_USER_DIR` | `~/.config/ibus/rime` | Rime 用户数据目录 |
| `RIME_SHARED_DIR` | `/usr/share/rime-data` | Rime 共享数据目录（一般不需要改） |

**已知限制：** `;` 键不会转发给 Rime。微软双拼用 `;` 输入 `ing` 的用户请换用自然码（`double_pinyin`）或其他不占用 `;` 的方案。

## 常用参数
```bash
./voice --streaming                          # 流式模式
./voice --streaming --device cuda            # GPU 加速
./voice --streaming --streaming-model zipformer-zh   # 中文流式模型
./voice --commands                           # 启用语音指令
./voice --ptt --ptt-hotkey f9               # 按键通话
./voice --list-devices                       # 列出音频设备
./voice --input-device "设备名"              # 指定麦克风
```

## 模型

首次运行自动下载到 `~/.cache/`。

**Whisper 精修模型：**

| 模型 | 大小 | 速度 | 适用场景 |
|------|------|------|----------|
| tiny | 39 MB | 最快 | 速记 |
| base | 74 MB | 快 | 日常使用 |
| small | 244 MB | 中等 | 平衡 |
| large-v3-turbo | ~1.5 GB | 快（GPU）| 流式修正首选 |

**流式模型（sherpa-onnx）：**

| 模型 | 大小 | 语言 |
|------|------|------|
| zipformer-en | ~80 MB | 英文 |
| zipformer-en-20M | ~20 MB | 英文（轻量） |
| zipformer-zh | ~488 MB | 中英双语 |

## 项目结构

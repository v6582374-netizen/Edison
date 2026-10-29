# YouTube StrataRead

🌐 中文 · [English](README.en.md)

一个终端工具，把任意 YouTube 视频转成一份「可思考、可深读」的 Bionic Reading 知识文档。

1. **抓取**：一条命令从 YouTube 下载原始 SRT 字幕。
2. **整理**：调用你自己的大模型 API（BYOK），用**一份可自定义的 prompt** 一次性完成翻译 + 去冗 + 分层标题化，生成只含正文的 Markdown。**全部 provider 默认启用深度思考**。
3. **阅读**：在终端以 Bionic Reading（词首字母加粗）风格逐句阅读；读完一个叶子自动跳到下一个，父标题自动打钩，并实时显示底部总进度条。

支持七类 Provider：**OpenAI**、**Anthropic (Claude)**、**Google Gemini**、**DeepSeek**、**MiniMax**、**GLM**，以及 **Compat（任意 OpenAI 兼容的第三方中转，可配置无限个命名 profile）**。仅支持 YouTube URL，本版本暂不支持本地字幕文件。

---

## 1. 系统要求

- macOS / Linux（Windows 建议用 WSL）。
- Python 3.10+（推荐 3.11）。
- 网络可访问 YouTube 以及你选择的 Provider 的 API。
- 无需 `ffmpeg`（只下载字幕）。

---

## 2. 安装

### 推荐：pipx（全局安装，无需手动管理虚拟环境）

```bash
pipx install youtube-strataread
by --help
```

### 从 GitHub 安装（最新开发版）

```bash
pipx install "git+https://github.com/vincentshuo/YouTube-StrataRead.git"
```

### 本地开发

```bash
# 使用 uv
uv venv --python 3.11 .venv
uv pip install -e '.[dev]'

# 或使用 pip
python3.11 -m venv .venv
.venv/bin/pip install -e '.[dev]'

source .venv/bin/activate
by --help
```

---

## 3. 先看一眼内置样例

不用配任何 key 就能体验阅读器：

```bash
by example                  # 默认 manual 模式（Tab 推进）
by example --mode stream    # 自动流式阅读
by example --path           # 打印样例路径
```

样例是一份预处理好的采访节目 Markdown（来自一个 YouTube 视频），展示完整的分层结构、Bionic Reading 效果，以及新的底部贴边阅读布局和面包屑/进度 footer。

---

## 4. 配置 Provider

### 七种 Provider + Compat Profiles

| Provider    | 说明                                     | 需要配置               |
| ----------- | ---------------------------------------- | ---------------------- |
| `openai`    | OpenAI 官方 API                           | `--key`                |
| `anthropic` | Anthropic Claude                         | `--key`                |
| `gemini`    | Google Gemini                            | `--key`                |
| `deepseek`  | DeepSeek 原生 API                        | `--key`                |
| `minimax`   | MiniMax 原生 API                         | `--key`                |
| `glm`       | 智谱 GLM 原生 API                        | `--key`                |
| `compat`    | 任意 OpenAI 兼容的第三方中转 / 代理       | `--key` + `--base-url` |

### 命令

```bash
by config set openai --key sk-...
by config set anthropic --key sk-ant-...
by config set gemini --key AIza...
by config set deepseek --key sk-...
by config set minimax --key sk-...
by config set glm --key sk-...

# compat 默认 profile（兼容旧命令）
by config set compat --key sk-... --base-url https://your-relay/v1
by config set compat --key sk-... --base-url https://your-relay/v1 --temperature off

# compat 命名 profile（推荐）
by config compat set aigocode --key sk-... --base-url https://api.aigocode.com/v1 --temperature on
by config compat set shenma --key sk-... --base-url https://api.whatai.cc/v1 --temperature off
by config compat use shenma
by config compat list

# 智谱翻译 Agent 前置层（非中文字幕 → 简体中文）
by config translation show
by config translation set --mode auto --agent general_translation --target-lang zh-CN

# 可选：改默认模型
by config set anthropic --key sk-ant-... --model claude-sonnet-4-5-20250929

by config use openai        # 切换默认 Provider（初始为 anthropic）
by config show              # 查看全部 Provider 当前配置（key 脱敏）
by config get gemini        # 查看单个 Provider
```

### 密钥存储（优先级从高到低）

1. 系统 **keyring**（macOS Keychain / Linux Secret Service）。
2. 环境变量 `BY_OPENAI_API_KEY`、`BY_ANTHROPIC_API_KEY`、`BY_GEMINI_API_KEY`、`BY_DEEPSEEK_API_KEY`、`BY_MINIMAX_API_KEY`、`BY_GLM_API_KEY`，以及 compat 的 `BY_COMPAT_<PROFILE>_API_KEY`（默认 profile 兼容 `BY_COMPAT_API_KEY`）。
3. 配置文件 `~/Library/Application Support/youtube-strataread/config.toml`（macOS）/ `~/.config/youtube-strataread/config.toml`（Linux）。

### 深度思考（全部 Provider 默认开启）

每家 Provider 都自动走各自的「长思考」路径：

- **OpenAI**：对 o-series / GPT-5 / 任何命中启发式的推理模型，自动传 `reasoning_effort="high"`。
- **Anthropic**：Claude 系列自动传 `thinking={"type": "enabled", "budget_tokens": 16000}`，`max_tokens=32000`，强制 `temperature=1.0`（官方硬性要求）。
- **Gemini**：Gemini 2.5 系列自动附加 `thinking_config(thinking_budget=-1)`（动态：模型自主决定思考时长）。
- **DeepSeek**：默认模型 `deepseek-reasoner`；若切到 `deepseek-chat`，自动附加 `thinking={"type": "enabled"}`，并隐藏 `reasoning_content`，只保留最终正文。
- **MiniMax**：默认模型 `MiniMax-M2.7`；自动附加 `reasoning_split=true`，把思考过程与正文拆开，只写入正文。
- **GLM**：默认模型 `glm-5.1`；自动附加 `thinking={"type": "enabled"}`，隐藏 `reasoning_content`，只保留最终正文。
- **Compat**：按模型名启发式，命中 `o1/o3/o4/gpt-5/deepseek-reasoner/thinking/r1` 即加 `reasoning_effort="high"`；不命中则保持原样。Compat profile 现在默认 **不发送** `temperature`，如确实需要再显式设 `--temperature on`。

所有 Provider 都使用**流式**请求，进度条实时显示已接收字符数，不会误以为卡死。

### 智谱翻译 Agent 前置层

当字幕不是中文时，`by process` / `by run` 默认会尝试使用智谱官方 Agent API 先翻译成简体中文，再把译文交给你现有的 prompt 做去冗和分层。默认 Agent 是 `general_translation`，因为它支持 `auto -> zh-CN`；它复用 `glm` 的 API Key（`by config set glm --key ...` 或 `BY_GLM_API_KEY`）。

- `mode=auto`（默认）：有 GLM key 且字幕非中文时调用 Agent；缺 key 或失败会回退到原字幕流程。
- `mode=force`：必须成功走 Agent，否则命令失败。
- `mode=off`：完全跳过前置翻译。

成功翻译后会在输出目录额外保存 `translated.txt`。可用 `--translation-mode auto|off|force` 和 `--translation-agent <agent_id>` 做单次覆盖。

---

## 5. 核心工作流

### 5.1 一键跑完：`by run`

```bash
by run https://www.youtube.com/watch?v=XXXXXXXXXXX
```

会依次弹出交互式菜单：
1. **Provider** — 从 7 种里选。
2. **Compat Profile** — 仅当你选择 `compat` 时出现。
3. **Model** — 每家都列出常见模型 + 「自定义」。
4. **Prompt** — 列出 `prompts/` 目录下所有 `.md` 文件，默认 `prompts.md` 排第一位。

选完后下载字幕 → 跑 AI → 进入阅读器。加 `--mode stream` 走自动流式阅读。

### 5.2 只抓字幕：`by fetch`

```bash
by fetch https://www.youtube.com/watch?v=XXXXXXXXXXX
# 产物：<当前目录>/<视频标题-slug>/raw.srt
```

### 5.3 下载 + AI 处理：`by process`

```bash
by process https://www.youtube.com/watch?v=XXXXXXXXXXX
```

产物：

```
<当前目录>/<视频标题-slug>/
├── raw.srt              # 原始字幕
└── <视频标题-slug>.md   # AI 终稿
```

常用参数：

- `--provider openai|anthropic|gemini|compat|deepseek|minimax|glm`（会跳过交互）
- `--compat-profile <名称>`（仅当 `--provider compat` 时使用）
- `--model <名称>`
- `--lang en`（指定源字幕语言）
- `--translation-mode auto|off|force`（控制智谱翻译 Agent 前置层）
- `--translation-agent general_translation`（单次指定翻译 Agent）
- `--overwrite` / `--suffix`（目录冲突策略）

### 5.4 阅读：`by read`

```bash
by read <slug 目录>/                    # 自动找里面的 .md
by read <slug 目录>/<slug>.md           # 直接传 md
by read <slug>.md --mode stream         # 自动流式
by read <slug>.md --mode stream --cpm 500
```

---

## 6. 阅读器操作说明

两种模式都使用同一套「层级下钻选择器」，并都带 Bionic Reading（词首字母加粗）渲染。

### 6.0 本次阅读器增强

- 底部常驻 footer 现在采用经典进度条样式：最下方是进度条，上方是当前章节面包屑，再上方固定留一行空隙。
- 正文区改为“底部锚定句子堆叠”：最新一句始终贴在 footer 上方两行，上一句会被稳定顶上去，不会再压住 footer。
- 正文仍然持续累积：跨章节自动推进、跳读、回看、重复阅读都会把正文历史继续向上堆叠；切换章节时会插入简洁分隔线。
- Markdown 标题不再写入正文历史；章节信息统一由 footer 面包屑承担。
- 中英/CJK 断句规则更细，逗号、分号、引号等场景阅读节奏更自然。

### 6.1 层级选择（共享）

```
(root)

▶ 1) [ ] 为什么 AI 是可承载的投资主题？
  2) [✓] 如何识别信号与噪声？

↑/↓ 或数字键选, Enter/Tab 进入, Esc/b 返回, h 回根, q 退出
```

- 数字键 `1..9`：直接选择。
- `↑ / ↓`：移动光标。
- `Enter / Tab`：进入当前节点；若为叶子则进入正文阅读。
- `Esc / b`：返回上一级。
- `h`：回根。
- `q`：退出并保存阅读进度。
- `[✓]` 表示该节点的**所有**叶子后代都已读完（父标题也会自动打钩）。

### 6.2 自动推进

- 进入任意叶子开始阅读后，读完该叶子会**自动跳到 DFS 顺序的下一个叶子**——不需要回菜单手动选。
- 到达本层最后一个叶子并读完时，自动跨越到上一层的下一个兄弟分支。
- 读完整份文档时弹回根菜单，所有节点都是 `[✓]`。
- 按 `Esc / b` 退出自动推进，回到当前叶子的父菜单。
- 菜单在独立屏幕中显示，不会擦掉主阅读屏上已经累积的正文历史。

### 6.3 Mode A：手动 Tab 推进（默认）

- `Tab`：显示下一句（逐字符浮现 + Bionic 粗体）。
- `Shift+Tab`：回看上一句。
- `Space`：跳到本小节末句。

### 6.4 Mode B：自动流式输出

- `Space`：暂停 / 继续。
- `+ / -`：调速（×0.5 / ×0.75 / ×1 / ×1.5 / ×2）。
- `Tab`：立即跳到当前句末并进入下一句。
- `Esc`：终止回上级。
- `--cpm N` / `--wpm N`：自定义速度（默认 300 CPM）。

### 6.5 阅读进度

自动保存在：

```
~/Library/Application Support/youtube-strataread/state/progress/<docHash>.json   # macOS
~/.local/state/youtube-strataread/progress/<docHash>.json                        # Linux
```

### 6.6 Footer 面包屑

- 面包屑位于进度条上方，并会根据当前终端宽度自动换行显示。
- 当终端宽度变化时，后续输出会继续按新的宽度换行，而不是截断为省略号。
- 进度条与面包屑分离，所以窄窗口下也不会互相挤压。

---

## 7. Prompt 系统

### 7.1 单 Prompt、一次到位

YouTube StrataRead 使用**一份** system prompt + 一次 LLM 调用完成全部分析。模型拿到整段字幕后自行完成翻译、去冗、分层标题化。不做分步、不做校验、不做自修复。

### 7.2 可编辑、可切换

- prompt 文件位于：`~/Library/Application Support/youtube-strataread/prompts/`（可用 `BY_PROMPTS_DIR` 覆盖）。
- 默认文件是 `prompts.md`，内容就是作者原始写的那份分析思路（首次运行自动生成）。
- **想新增 prompt 类型**：直接在该目录里放一个新的 `.md`（比如 `podcast.md`、`lecture.md`）——下一次 `by run` 就会自动在菜单里看到它，让你选择。

### 7.3 命令

```bash
by prompts path       # 打印 prompts.md 路径
by prompts show       # 打印当前 prompts.md 内容
by prompts reset      # 恢复默认 prompt
open "$(dirname $(by prompts path))"   # 用 Finder 打开目录
```

---

## 8. 命令速查表

| 命令                                               | 说明                                       |
| -------------------------------------------------- | ------------------------------------------ |
| `by example [--mode stream]`                       | 读取内置样例（无需 key）                   |
| `by fetch <URL>`                                   | 仅下载字幕                                 |
| `by process <URL>`                                 | 下载 + AI（交互式选 Provider/Model/Prompt）|
| `by run <URL>`                                     | `process` + `read` 一条龙                  |
| `by read <MD 或 slug 目录>`                        | 进入阅读器                                 |
| `by config set <provider> --key X [--base-url Y]`  | 配置 provider key / base_url / model       |
| `by config use <provider>`                         | 切换默认 Provider                          |
| `by config show`                                   | 查看所有 Provider 配置                     |
| `by prompts path \| show \| reset`                 | 管理 prompt 文件                           |

全局 flag：`-v/--verbose`、`--no-color`、`--config PATH`。

---

## 9. 产物位置一览

- **AI 产物**：`<当前目录>/<视频-slug>/{raw.srt, <slug>.md}`
- **配置文件**：`~/Library/Application Support/youtube-strataread/config.toml`
- **Prompt 目录**：`~/Library/Application Support/youtube-strataread/prompts/`
- **阅读进度**：`~/Library/Application Support/youtube-strataread/state/progress/`
- **崩溃日志**：`<slug>/.by-crash-<timestamp>.log`（仅当 AI 处理失败时）
- **内置样例**：`by example --path` 直接打印位置

---

## 10. FAQ

**Q: `zsh: command not found: by`** — 未安装或虚拟环境未激活。推荐 `pipx install youtube-strataread`，或 `source .venv/bin/activate`。

**Q: `missing API key for provider 'xxx'`** — 固定 provider 运行 `by config set <provider> --key <KEY>`，compat profile 运行 `by config compat set <name> --key <KEY>`；也可以设置对应的环境变量。

**Q: `compat provider needs a base_url`** — 跑 `by config set compat --key ... --base-url https://your-relay/v1`，或 `by config compat set <name> --key ... --base-url https://your-relay/v1`。

**Q: 为什么 compat 默认不发 `temperature`？** — 某些第三方中转上的 Claude / Opus / 推理模型会拒绝 `temperature`。为了兼容性，compat profile 默认 `temperature=off`；只有明确需要时再用 `--temperature on` 打开。

**Q: 视频没有字幕** — 工具会提示 `no subtitles ...` 并优雅退出，不创建产物目录。

**Q: AI 处理失败但 `raw.srt` 已下载** — 在产物目录里会出现 `.by-crash-<时间戳>.log`；加 `--overwrite` 重跑即可。

---

## 11. License

MIT。

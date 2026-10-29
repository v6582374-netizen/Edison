# YouTube StrataRead

🌐 [中文](README.md) · English

A terminal tool that turns any YouTube video into a "deep-readable" Bionic
Reading knowledge document.

1. **Fetch** the raw SRT subtitles from YouTube.
2. **Outline** them with AI (BYOK) via a **single user-editable prompt** that
   handles translation, redundancy cleanup, and hierarchical outlining in one
   shot — producing a Markdown with just the body text. **Deep thinking is
   enabled by default across every provider.**
3. **Read** the result in the terminal with Bionic Reading emphasis (first
   letters of each word bolded). After finishing one leaf you are auto-advanced
   to the next; parents get a check mark once all descendants are read, and a
   sticky bottom progress bar tracks the whole document.

Seven provider families are supported: **OpenAI**, **Anthropic (Claude)**,
**Google Gemini**, **DeepSeek**, **MiniMax**, **GLM**, plus **Compat**
(any OpenAI-compatible third-party relay, with unlimited named profiles).
Only YouTube URLs are supported in this version; local subtitle files are not.

---

## 1. Requirements

- macOS / Linux (Windows works via WSL).
- Python 3.10+ (3.11 recommended).
- Network access to YouTube and your chosen provider's API.
- `ffmpeg` is **not** required — we only download subtitles.

---

## 2. Installation

### Recommended: pipx (global install, no manual venv)

```bash
pipx install youtube-strataread
by --help
```

### From GitHub (latest dev version)

```bash
pipx install "git+https://github.com/vincentshuo/Edison.git"
```

### Local development

```bash
# using uv
uv venv --python 3.11 .venv
uv pip install -e '.[dev]'

# or plain pip
python3.11 -m venv .venv
.venv/bin/pip install -e '.[dev]'

source .venv/bin/activate
by --help
```

---

## 3. Try the bundled sample first (no API key needed)

```bash
by example                  # manual mode (Tab-to-advance)
by example --mode stream    # auto streaming mode
by example --path           # print the sample location on disk
```

The sample is a pre-processed interview outline bundled inside the package —
ideal for evaluating the reader UX, including the new bottom-anchored layout
and breadcrumb/progress footer, without touching any cloud API.

---

## 4. Configure a provider

### Seven fixed providers + compat profiles

| Provider    | Purpose                                      | Required config          |
| ----------- | -------------------------------------------- | ------------------------ |
| `openai`    | OpenAI's own API                             | `--key`                  |
| `anthropic` | Anthropic Claude                             | `--key`                  |
| `gemini`    | Google Gemini                                | `--key`                  |
| `deepseek`  | DeepSeek native API                          | `--key`                  |
| `minimax`   | MiniMax native API                           | `--key`                  |
| `glm`       | GLM native API                               | `--key`                  |
| `compat`    | Any OpenAI-compatible third-party relay      | `--key` + `--base-url`   |

### Commands

```bash
by config set openai --key sk-...
by config set anthropic --key sk-ant-...
by config set gemini --key AIza...
by config set deepseek --key sk-...
by config set minimax --key sk-...
by config set glm --key sk-...

# default compat profile (backwards-compatible)
by config set compat --key sk-... --base-url https://your-relay/v1
by config set compat --key sk-... --base-url https://your-relay/v1 --temperature off

# named compat profiles
by config compat set aigocode --key sk-... --base-url https://api.aigocode.com/v1 --temperature on
by config compat set shenma --key sk-... --base-url https://api.whatai.cc/v1 --temperature off
by config compat use shenma
by config compat list

# Zhipu translation Agent preprocessor (non-Chinese subtitles → Simplified Chinese)
by config translation show
by config translation set --mode auto --agent general_translation --target-lang zh-CN

# optional: override the default model
by config set anthropic --key sk-ant-... --model claude-sonnet-4-5-20250929

by config use openai        # switch default provider (initial: anthropic)
by config show              # inspect every provider (keys are masked)
by config get gemini        # inspect one provider
```

### Where keys live (priority order)

1. System **keyring** (macOS Keychain / Linux Secret Service).
2. Environment variables: `BY_OPENAI_API_KEY`, `BY_ANTHROPIC_API_KEY`,
   `BY_GEMINI_API_KEY`, `BY_DEEPSEEK_API_KEY`, `BY_MINIMAX_API_KEY`,
   `BY_GLM_API_KEY`, plus compat `BY_COMPAT_<PROFILE>_API_KEY`
   (the default profile also accepts legacy `BY_COMPAT_API_KEY`).
3. Config file: `~/Library/Application Support/youtube-strataread/config.toml`
   (macOS) / `~/.config/youtube-strataread/config.toml` (Linux).

### Deep thinking (on by default for every provider)

Each provider is wired to its own "think hard" path:

- **OpenAI** — for o-series / GPT-5 / anything the heuristic recognises as a
  reasoning model, `reasoning_effort="high"` is passed automatically.
- **Anthropic** — Claude models auto-receive
  `thinking={"type": "enabled", "budget_tokens": 16000}` with
  `max_tokens=32000` and a forced `temperature=1.0` (Anthropic requirement).
- **Gemini** — Gemini 2.5 models include
  `thinking_config(thinking_budget=-1)` (dynamic budget; the model decides).
- **DeepSeek** — defaults to `deepseek-reasoner`; if you switch to
  `deepseek-chat`, we automatically add `thinking={"type": "enabled"}` and
  hide `reasoning_content` from the final Markdown.
- **MiniMax** — defaults to `MiniMax-M2.7`; we automatically enable
  `reasoning_split=true` and keep only the visible answer.
- **GLM** — defaults to `glm-5.1`; we automatically enable
  `thinking={"type": "enabled"}` and hide `reasoning_content`.
- **Compat** — if the model name matches `o1/o3/o4/gpt-5/deepseek-reasoner/
  thinking/r1`, `reasoning_effort="high"` is forwarded; otherwise we leave
  things alone so strict relays don't 400. Compat profiles now default to
  **not sending** `temperature`; explicitly enable it with `--temperature on`
  only when the relay/model needs it.

Every provider streams the response, so the progress bar ticks in real time
and you can see deltas arrive instead of staring at a frozen spinner.

### Zhipu translation Agent preprocessor

When subtitles are not Chinese, `by process` / `by run` now attempts to translate
them to Simplified Chinese through Zhipu's official Agent API before handing the
text to your existing prompt for cleanup and outlining. The default Agent is
`general_translation` because it supports `auto -> zh-CN`; it reuses the `glm`
API key (`by config set glm --key ...` or `BY_GLM_API_KEY`).

- `mode=auto` (default): use the Agent when a GLM key is available; fall back to
  the original transcript if the key is missing or the Agent fails.
- `mode=force`: require the Agent path; failures stop the command.
- `mode=off`: skip the preprocessor entirely.

Successful translations are saved as `translated.txt` in the output directory.
Use `--translation-mode auto|off|force` and `--translation-agent <agent_id>` for
one-off overrides.

---

## 5. Core workflow

### 5.1 One-shot: `by run`

```bash
by run https://www.youtube.com/watch?v=XXXXXXXXXXX
```

Interactive menus appear in order:
1. **Provider** — pick one of the seven.
2. **Compat Profile** — only appears when `compat` is chosen.
3. **Model** — a per-provider catalog plus a "custom..." entry.
4. **Prompt** — every `.md` file under the prompts directory (default
   `prompts.md` pinned first).

After that: SRT download → AI → reader. Pass `--mode stream` for auto
streaming.

### 5.2 Only download: `by fetch`

```bash
by fetch https://www.youtube.com/watch?v=XXXXXXXXXXX
# produces: <cwd>/<video-slug>/raw.srt
```

### 5.3 Download + AI: `by process`

```bash
by process https://www.youtube.com/watch?v=XXXXXXXXXXX
```

Layout:

```
<cwd>/<video-slug>/
├── raw.srt              # yt-dlp original subtitle
└── <video-slug>.md      # AI final draft
```

Useful flags:

- `--provider openai|anthropic|gemini|compat|deepseek|minimax|glm`
  (skips the interactive picker)
- `--compat-profile <name>` (only with `--provider compat`)
- `--model <name>`
- `--lang en` (preferred source subtitle language)
- `--translation-mode auto|off|force` (control the Zhipu translation Agent preprocessor)
- `--translation-agent general_translation` (override the translation Agent for one run)
- `--overwrite` / `--suffix` (folder collision strategy)

### 5.4 Read: `by read`

```bash
by read <slug-folder>/                  # auto-discovers the .md inside
by read <slug-folder>/<slug>.md         # or pass the .md directly
by read <slug>.md --mode stream         # auto streaming
by read <slug>.md --mode stream --cpm 500
```

---

## 6. Reader controls

Both modes share the same hierarchical picker and both apply Bionic Reading
(first-letter-bold) styling.

### 6.0 What's new in the reader

- The bottom of the terminal now uses a classic progress bar again: progress on the last row, breadcrumb above it, and one fixed spacer row above both.
- The body now uses a bottom-anchored sentence stack: the newest sentence stays two rows above the footer and older sentences are pushed upward without overlapping it.
- Body history still accumulates across sections. Auto-advance, revisits, and re-reading keep stacking visible body text upward, with a lightweight divider between leaf replays/transitions.
- Markdown headings are no longer written into body history; the breadcrumb footer is the section label.
- Sentence splitting is more natural for English and CJK punctuation, including commas, semicolons, and closing quotes.

### 6.1 Shared hierarchical selector

```
(root)

▶ 1) [ ] Why is AI a durable investment theme?
  2) [✓] How to separate signal from noise?

↑/↓ or digits to pick, Enter/Tab to enter, Esc/b to go up, h to root, q to quit
```

- Digit keys `1..9`: jump directly.
- `↑ / ↓`: move cursor.
- `Enter / Tab`: descend; if leaf, enter body reading.
- `Esc / b`: step up one level.
- `h`: jump to root.
- `q`: quit (progress is saved).
- `[✓]` marks a node whose entire leaf subtree has been read (parents bubble
  up automatically).

### 6.2 Auto-advance

- After you finish a leaf, the navigator **automatically yields the next leaf
  in DFS order** — no menu in between.
- When the last sibling at one level is done, it crosses the boundary up to
  the next parent sibling.
- After the whole document is read you are bounced back to the root menu with
  everything `[✓]`.
- Press `Esc / b` during reading to exit auto-advance and return to the
  parent menu of the current leaf.
- The menu lives on an alternate screen, so opening it never erases the
  accumulated body history on the main reading screen.

### 6.3 Mode A — manual Tab (default)

- `Tab` — reveal the next sentence (typed out char-by-char + Bionic bold).
- `Shift+Tab` — re-show the previous sentence.
- `Space` — jump to the last sentence of the current leaf.

### 6.4 Mode B — auto stream

- `Space` — pause / resume.
- `+` / `-` — speed tier (×0.5 / ×0.75 / ×1 / ×1.5 / ×2).
- `Tab` — skip to the end of the current sentence and move on.
- `Esc` — terminate and go back up.
- `--cpm N` / `--wpm N` — override the base speed (default 300 CPM).

### 6.5 Progress persistence

Progress is saved at:

```
~/Library/Application Support/youtube-strataread/state/progress/<docHash>.json   # macOS
~/.local/state/youtube-strataread/progress/<docHash>.json                        # Linux
```

### 6.6 Footer breadcrumb

- The breadcrumb sits above the progress bar and wraps to the current terminal width instead of truncating with ellipses.
- When the terminal is resized, subsequent output continues wrapping to the new width.
- The progress bar sits on a separate row, so narrow windows do not force the breadcrumb and progress to compete for width.

---

## 7. The prompt system

### 7.1 One prompt, one call

YouTube StrataRead feeds the full transcript to the LLM **once** with a single
system prompt. The model handles translation, denoising, and hierarchical
outlining in one shot — no validators, no self-repair, no multi-step
orchestration.

### 7.2 Editable and switchable

- Prompts live at `~/Library/Application Support/youtube-strataread/prompts/`
  (override via `BY_PROMPTS_DIR`).
- The default file `prompts.md` is materialised on first run — its content is
  the author's original analysis blueprint, byte-for-byte.
- **Adding a new prompt**: drop any `.md` file (e.g. `podcast.md`,
  `lecture.md`) into the directory. The next `by run` will list it in the
  picker automatically — no CLI step required.

### 7.3 Commands

```bash
by prompts path       # print the full path of prompts.md
by prompts show       # dump the current prompts.md
by prompts reset      # restore defaults
open "$(dirname $(by prompts path))"   # open the directory in Finder
```

---

## 8. Command cheatsheet

| Command                                             | Purpose                                      |
| --------------------------------------------------- | -------------------------------------------- |
| `by example [--mode stream]`                        | Read the bundled sample (no API key needed) |
| `by fetch <URL>`                                    | Download subtitles only                      |
| `by process <URL>`                                  | Download + AI (interactive picker)           |
| `by run <URL>`                                      | `process` + `read` in one shot               |
| `by read <MD-or-slug>`                              | Open the reader                              |
| `by config set <provider> --key X [--base-url Y]`   | Save key / base_url / model for a provider   |
| `by config compat set <name> --key X --base-url Y`  | Save a named compat relay profile            |
| `by config compat list \| get \| use`               | Inspect / select compat profiles             |
| `by config use <provider>`                          | Change the default provider                  |
| `by config show`                                    | Inspect every provider                       |
| `by prompts path \| show \| reset`                  | Manage the prompt file                       |

Global flags: `-v/--verbose`, `--no-color`, `--config PATH`.

---

## 9. Artefact locations

- **AI output**: `<cwd>/<video-slug>/{raw.srt, <slug>.md}`
- **Config file**: `~/Library/Application Support/youtube-strataread/config.toml`
- **Prompts dir**: `~/Library/Application Support/youtube-strataread/prompts/`
- **Reader progress**: `~/Library/Application Support/youtube-strataread/state/progress/`
- **Crash log**: `<slug>/.by-crash-<timestamp>.log` (only on AI failure)
- **Bundled sample**: `by example --path` prints its location

---

## 10. FAQ

**Q: `zsh: command not found: by`** — not installed or venv not active.
Recommended: `pipx install youtube-strataread`, or `source .venv/bin/activate`.

**Q: `missing API key for provider 'xxx'`** — for fixed providers, run
`by config set <provider> --key <KEY>` or export `BY_<PROVIDER>_API_KEY`.
For compat profiles, run `by config compat set <name> --key <KEY>` or export
`BY_COMPAT_<PROFILE>_API_KEY`.

**Q: `compat provider needs a base_url`** — run
`by config set compat --key ... --base-url https://your-relay/v1`, or
`by config compat set <name> --key ... --base-url https://your-relay/v1`.

**Q: Why does compat default to no `temperature`?** — some third-party relay
backends reject `temperature` for Claude / Opus / reasoning-class models. To
maximize compatibility, compat profiles default to `temperature=off`; enable
it explicitly with `--temperature on` only when needed.

**Q: The video has no subtitles** — the tool prints
`no subtitles ...` and exits cleanly; no output directory is created.

**Q: AI fails but `raw.srt` is already on disk** — a
`.by-crash-<timestamp>.log` lands in the output folder; re-run with
`--overwrite`.

---

## 11. License

MIT.

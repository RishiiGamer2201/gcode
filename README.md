# gcode

A tiny agentic coding CLI on the OpenAI API — like Codex CLI or Claude Code, but ~400 lines
of Python you can read in one sitting, and it logs every token you spend.

Point it at a repo, tell it what to do, and it reads files, edits them, runs commands, and
checks its own work — asking permission before it writes anything or runs a shell command.

```
$ gcode "add retries to the fetch() in api.py and run the tests"
  * read_file {"path": "api.py"}
  * edit_file {"path": "api.py", "old_string": "def fetch(url):", ...}

  allow: edit C:\work\myrepo\api.py
  [y/N] y
  * run_shell {"command": "pytest -q"}

Added a 3-attempt exponential backoff to fetch(). 14 tests pass.
$0.0231 this run
```

## What it does

| | |
|---|---|
| Tools | `read_file`, `write_file`, `edit_file`, `list_dir`, `run_shell` |
| Safety | every write and shell command asks `[y/N]` first; file access is locked to the current directory |
| Cost tracking | every API call logged to `~/.gcode/usage.jsonl` with tokens, USD, model, user, project |
| Budget | `--budget 0.50` stops the agent mid-task once it has spent $0.50 |
| Modes | interactive chat, or one-shot `gcode "do the thing"` |
| Config | `.env` auto-loaded from the current folder, then from the install folder |

---

## Setup

### 1. Get Python 3.9+

```bash
python --version    # Windows: py -3 --version
```

### 2. Clone and install

```bash
git clone https://github.com/RishiiGamer2201/gcode.git
cd gcode
pip install -e .
```

That puts a `gcode` command on your PATH and installs the `openai` package.

**Install it globally, not in a venv.** You want to run `gcode` inside *other* projects, and
a venv-installed command only exists while that venv is active. Options, best first:

```bash
pipx install -e .          # isolated, still on PATH everywhere  (pip install pipx)
pip install -e .           # into your main/base Python — simplest
```

`-e` (editable) means `git pull` updates your install; no reinstall needed.

Verify from some *other* folder:

```bash
cd ~/some/other/project
gcode --usage
# -> no usage logged yet: C:\Users\you\.gcode\usage.jsonl
```

If `gcode` is "not recognized", you installed it into a venv that isn't active. Either
activate it, or reinstall globally as above. Fallback that always works:
`python C:\path\to\gcode\gcode.py "your prompt"`.

### 3. Set the API key

The key is the one thing that is **not** in this repo. Get it from whoever owns the OpenAI
account. Easiest way — a `.env` file, which gcode loads automatically:

```bash
cp .env.example .env
```

Then edit `.env` and put the real key in. gcode looks for `.env` in two places:

1. the folder you run `gcode` from — per-project overrides
2. the folder `gcode.py` lives in — your global default

Never commit it; `.gitignore` already excludes `.env`.

Environment variables also work and **override** `.env`:

```bash
# macOS / Linux — add to ~/.bashrc or ~/.zshrc
export OPENAI_API_KEY="sk-proj-..."
export GCODE_USER="your-name"      # so the team log knows whose spend is whose
```

```powershell
# Windows PowerShell — this session only
$env:OPENAI_API_KEY = "sk-proj-..."

# Windows PowerShell — permanent
setx OPENAI_API_KEY "sk-proj-..."
setx GCODE_USER "your-name"
```

`setx` writes to the registry for *future* processes — the shell you typed it in still won't
see it. Open a new terminal after `setx`, or use `$env:` for the current one. `export` is
bash syntax and does not exist in PowerShell.

### 4. Run it

```bash
cd /path/to/the/project/you/want/to/work/on
gcode                                   # interactive
gcode "why does test_login fail?"       # one-shot
```

The agent's file access is scoped to the directory you launch it from. Launch it from the
repo you want it to touch, not from your home folder.

---

## Usage

```bash
gcode                       # chat mode
gcode "fix the bug in x.py" # one-shot, exits when done
gcode -m gpt-5-mini "..."   # cheaper model
gcode --budget 0.25 "..."   # stop after $0.25
gcode --yolo "..."          # no approval prompts (only in a git repo you can reset)
gcode --usage               # spend, last 30 days
gcode --usage 7             # spend, last 7 days
```

In chat mode:

| command | |
|---|---|
| `/reset` | clear the conversation, keep the session |
| `/cost` | spend so far this session |
| `/usage` | full 30-day report |
| `/exit` | quit |

---

## Tracking usage

This was the whole point. OpenAI's dashboard shows you a total for the whole key; it does
not tell you *who* on the team spent it or *which project* it went to. gcode logs that
locally on every call.

`~/.gcode/usage.jsonl`, one line per API call:

```json
{"ts":"2026-08-18T14:31:02","user":"rishii","model":"gpt-5","in":8421,"out":344,"usd":0.013966,"cwd":"C:\\work\\myrepo","tag":"chat"}
```

```bash
gcode --usage
```

```
gcode usage, last 30 days | 84 API calls | 612,004 in / 21,338 out tokens | $0.9612

  by day
    2026-08-18                   $0.6120
    2026-08-17                   $0.3492

  by model
    gpt-5                        $0.9107
    gpt-5-mini                   $0.0505

  by user
    rishii                       $0.7431
    sarthak                      $0.2181
```

It is plain JSONL, so any other question is one command away:

```bash
# spend per project
cat ~/.gcode/usage.jsonl | jq -r '"\(.usd) \(.cwd)"' | sort -rn | head
```

**Team-wide totals:** each person's log lives on their own machine. To roll them up, have
everyone commit their `usage.jsonl` to a shared repo, or point `GCODE_HOME` at a shared
drive. For the authoritative billing number, the OpenAI dashboard is still the source of
truth — this is a per-person breakdown, not a bill.

**Prices** are a hardcoded table at the top of [`gcode.py`](gcode.py) (USD per 1M tokens).
When OpenAI changes them, edit that dict — or override without touching the code:

```bash
export GCODE_PRICES='{"gpt-5":[1.25,10.00]}'
```

An unknown model logs `usd: 0` rather than crashing, so add new models to the table when
you start using them.

---

## Keeping the bill small

The agent re-sends the whole conversation on every tool call, so a long session costs
roughly O(n²) in tokens. What actually helps:

1. **`/reset` between unrelated tasks.** The single biggest lever. A stale 60-message
   context gets re-billed on every turn.
2. **`gpt-5-mini` for grunt work** (renames, boilerplate, "what does this file do") — ~5x
   cheaper. Save `gpt-5` for real debugging.
3. **Use one-shot mode** for one-shot jobs: `gcode "..."` starts from an empty context.
4. **Be specific about files.** "fix the retry logic in api.py:40" costs a fraction of "fix
   the retry logic" — the agent doesn't have to read half the repo to find it.
5. **`--budget`** as a seatbelt when you let it run unattended.

Prompt caching (the ~10x discount on repeated prefixes) is automatic on the OpenAI side and
is reflected in the logged cost.

---

## Safety

- **Approval prompts.** Every `write_file`, `edit_file`, and `run_shell` asks first, showing
  the exact path or command. `--yolo` turns this off — only use it inside a git repo with a
  clean tree, so `git checkout .` undoes any mess.
- **Directory sandbox.** File tools resolve paths and refuse anything outside the directory
  you launched from. Note this is a guard on the *file tools* — `run_shell` runs real shell
  commands and can go anywhere. That's why shell commands need approval.
- **The key.** Never commit it. `.gitignore` covers `.env`. If it leaks, rotate it in the
  OpenAI dashboard immediately — a leaked key spends real money.
- **Company code.** Anything the agent reads goes to OpenAI. Check that's allowed before
  pointing it at a private repo.

---

## Development

```bash
python test_gcode.py    # self-check, no pytest needed
```

Covers the file tools, the path sandbox, approval denial, the cost math, and the usage log.

The whole thing is one file. To add a tool: append a schema to `TOOLS` and a branch to
`run_tool()`. That's it — there's no plugin system and doesn't need one.

## Why not just use Codex CLI?

Use it if it fits — `npm i -g @openai/codex` is a more polished tool. Build on this one when
you want to see exactly what's sent to the API, control the system prompt, or get per-person
cost attribution that the official tools don't give you.

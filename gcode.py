#!/usr/bin/env python3
"""gcode - a tiny agentic coding CLI on the OpenAI API.

Like codex/claude-code, but ~400 lines and it logs every token you spend.
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

import openai
from openai import OpenAI

ENV_FILES = [pathlib.Path.cwd() / ".env", pathlib.Path(__file__).resolve().parent / ".env"]


def load_dotenv(paths=ENV_FILES):
    """Read KEY=VALUE lines from .env files. Real env vars always win."""
    loaded = []
    for path in paths:
        if not path.is_file():
            continue
        loaded.append(path)
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            val = val.split(" #")[0].strip().strip("\"'")  # drop trailing comments
            if val:
                os.environ.setdefault(key.strip(), val)
    return loaded


LOADED_ENV = load_dotenv()


def num_env(name, default=0.0):
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


HOME = pathlib.Path(os.environ.get("GCODE_HOME", pathlib.Path.home() / ".gcode"))
USAGE_LOG = HOME / "usage.jsonl"
DEFAULT_MODEL = os.environ.get("GCODE_MODEL", "gpt-5.4")
MAX_OUTPUT_CHARS = 20000

# USD per 1M tokens: (input, cached input, output).
# Source: https://developers.openai.com/api/docs/pricing, checked 2026-08-18.
# Update when OpenAI changes prices, or override without editing code:
#   GCODE_PRICES='{"gpt-5.6-sol":[3.0,0.3,20.0]}'
PRICES = {
    "gpt-5.5": (5.00, 0.50, 30.00),
    "gpt-5.4": (2.50, 0.25, 15.00),
    "gpt-5.4-mini": (0.75, 0.075, 4.50),
    "gpt-5.4-nano": (0.20, 0.02, 1.25),
    "gpt-5.3-codex": (1.75, 0.175, 14.00),
    "gpt-5.2": (1.75, 0.175, 14.00),
    "gpt-5.1": (1.25, 0.125, 10.00),
    "gpt-5": (1.25, 0.125, 10.00),
    "gpt-5-mini": (0.25, 0.025, 2.00),
    "gpt-5-nano": (0.05, 0.005, 0.40),
    "gpt-4.1": (2.00, 0.50, 8.00),
    "gpt-4.1-mini": (0.40, 0.10, 1.60),
    "gpt-4o": (2.50, 1.25, 10.00),
    "gpt-4o-mini": (0.15, 0.075, 0.60),
    "o4-mini": (1.10, 0.275, 4.40),
}
PRICES.update(json.loads(os.environ.get("GCODE_PRICES", "{}")))

SYSTEM = """You are gcode, a coding agent running in the user's terminal.

Working directory: {cwd}
Platform: {platform}

Rules:
- Use the tools to read real files before you edit them. Never guess file contents.
- Prefer edit_file (exact string replace) over rewriting a whole file.
- Keep changes minimal and match the surrounding code style.
- Run tests/build commands with run_shell when it helps verify your work.
- When done, reply with a short summary. No essays.
"""

TOOLS = [
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a UTF-8 text file. Returns numbered lines.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer", "description": "1-based first line"},
            "limit": {"type": "integer", "description": "max lines (default 400)"},
        }, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Create or overwrite a file with the given content.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"},
        }, "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": "Replace an exact string in a file. old_string must appear exactly once.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "old_string": {"type": "string"},
            "new_string": {"type": "string"},
        }, "required": ["path", "old_string", "new_string"]}}},
    {"type": "function", "function": {
        "name": "list_dir",
        "description": "List files in a directory (non-recursive).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "default '.'"},
        }}}},
    {"type": "function", "function": {
        "name": "run_shell",
        "description": "Run a shell command in the working directory. Use for grep/find/tests/git.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"},
            "timeout": {"type": "integer", "description": "seconds, default 120"},
        }, "required": ["command"]}}},
]


def safe_path(p):
    """Keep the agent inside the working directory."""
    root = pathlib.Path.cwd().resolve()
    full = (root / p).resolve()
    if full != root and root not in full.parents:
        raise ValueError("path escapes working directory: " + str(p))
    return full


def confirm(what, yolo):
    if yolo:
        return True
    ans = input("\n  allow: " + what + "\n  [y/N] ").strip().lower()
    return ans in ("y", "yes")


def clip(s):
    return s if len(s) <= MAX_OUTPUT_CHARS else s[:MAX_OUTPUT_CHARS] + "\n...[truncated]"


def run_tool(name, args, yolo):
    try:
        if name == "read_file":
            path = safe_path(args["path"])
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            start = max(1, args.get("offset", 1))
            end = start + args.get("limit", 400)
            body = "\n".join("%d\t%s" % (i, l)
                             for i, l in enumerate(lines[start - 1:end - 1], start))
            return clip(body) or "(empty file)"

        if name == "write_file":
            path = safe_path(args["path"])
            if not confirm("write " + str(path), yolo):
                return "DENIED by user"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(args["content"], encoding="utf-8")
            return "wrote %s (%d chars)" % (path, len(args["content"]))

        if name == "edit_file":
            path = safe_path(args["path"])
            text = path.read_text(encoding="utf-8")
            n = text.count(args["old_string"])
            if n == 0:
                return "ERROR: old_string not found"
            if n > 1:
                return "ERROR: old_string appears %d times, must be unique" % n
            if not confirm("edit " + str(path), yolo):
                return "DENIED by user"
            path.write_text(text.replace(args["old_string"], args["new_string"]),
                            encoding="utf-8")
            return "edited " + str(path)

        if name == "list_dir":
            path = safe_path(args.get("path", "."))
            out = []
            for e in sorted(path.iterdir()):
                if e.name in (".git", "node_modules", "__pycache__", ".venv"):
                    continue
                out.append(e.name + "/" if e.is_dir() else e.name)
            return clip("\n".join(out)) or "(empty dir)"

        if name == "run_shell":
            cmd = args["command"]
            if not confirm("run: " + cmd, yolo):
                return "DENIED by user"
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                               timeout=args.get("timeout", 120))
            return clip("exit=%d\n%s%s" % (r.returncode, r.stdout, r.stderr))

        return "unknown tool " + name
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out"
    except Exception as e:  # tool errors go back to the model, not the user
        return "ERROR: %s: %s" % (type(e).__name__, e)


_warned_models = set()


def price_key(model):
    """Longest matching family, but only on a '-' boundary.

    Keeps gpt-5.6-luna from being priced as gpt-5 just because it starts with it.
    """
    for k in sorted(PRICES, key=len, reverse=True):
        if model == k or model.startswith(k + "-"):
            return k
    return None


def cost(model, usage):
    key = price_key(model)
    if not key:
        if model not in _warned_models:
            _warned_models.add(model)
            print("  \033[33mno price listed for %s - logging $0. "
                  "Add it to PRICES or set GCODE_PRICES.\033[0m" % model)
        return 0.0
    pin, pcached, pout = PRICES[key]
    cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0
    fresh = usage.prompt_tokens - cached
    return (fresh * pin + cached * pcached + usage.completion_tokens * pout) / 1_000_000


def log_usage(model, usage, tag):
    c = cost(model, usage)
    HOME.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "user": os.environ.get("GCODE_USER") or os.environ.get("USERNAME")
                or os.environ.get("USER") or "?",
        "model": model,
        "in": usage.prompt_tokens,
        "out": usage.completion_tokens,
        "usd": round(c, 6),
        "cwd": str(pathlib.Path.cwd()),
        "tag": tag,
    }
    with USAGE_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return c


def show_usage(days):
    if not USAGE_LOG.exists():
        print("no usage logged yet: " + str(USAGE_LOG))
        return
    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    by_day, by_model, by_user = {}, {}, {}
    total, calls, tin, tout = 0.0, 0, 0, 0
    for line in USAGE_LOG.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        day = r["ts"][:10]
        if day < cutoff:
            continue
        calls += 1
        total += r["usd"]
        tin += r["in"]
        tout += r["out"]
        by_day[day] = by_day.get(day, 0) + r["usd"]
        by_model[r["model"]] = by_model.get(r["model"], 0) + r["usd"]
        by_user[r["user"]] = by_user.get(r["user"], 0) + r["usd"]

    print("\ngcode usage, last %d days | %d API calls | %s in / %s out tokens | $%.4f\n"
          % (days, calls, f"{tin:,}", f"{tout:,}", total))
    for title, d in (("by day", by_day), ("by model", by_model), ("by user", by_user)):
        print("  " + title)
        for k, v in sorted(d.items(), key=lambda kv: -kv[1]):
            print("    %-28s $%.4f" % (k, v))
        print()
    print("  log: " + str(USAGE_LOG))


def agent_turn(client, model, messages, yolo, budget, spent):
    """Run one user turn to completion. Returns dollars spent this turn."""
    turn_cost = 0.0
    while True:
        try:
            resp = client.chat.completions.create(model=model, messages=messages, tools=TOOLS)
        except openai.APIError as e:
            # One readable line, then back to the prompt. Never dump a traceback.
            msg = getattr(getattr(e, "body", None), "get", lambda k: None)("message") or str(e)
            print("\n\033[31mAPI error (%s): %s\033[0m" % (type(e).__name__, msg))
            if "must be verified" in msg or "model_not_found" in msg:
                print("Try another model: gcode --models   |   gcode -m gpt-5.4-mini")
            return turn_cost
        turn_cost += log_usage(model, resp.usage, "chat")
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))

        if msg.content:
            print("\n" + msg.content + "\n")

        if not msg.tool_calls:
            return turn_cost

        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments or "{}")
            print("  \033[90m* %s %s\033[0m" % (tc.function.name,
                                                json.dumps(args)[:120]))
            result = run_tool(tc.function.name, args, yolo)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

        if budget and spent + turn_cost >= budget:
            messages.append({"role": "user",
                             "content": "Budget reached. Stop calling tools and summarize."})
            budget = None


def main():
    ap = argparse.ArgumentParser(prog="gcode",
                                 description="agentic coding CLI on the OpenAI API")
    ap.add_argument("prompt", nargs="*", help="one-shot prompt; omit for interactive chat")
    ap.add_argument("-m", "--model", default=DEFAULT_MODEL)
    ap.add_argument("--yolo", action="store_true",
                    help="skip approval prompts for writes and shell commands")
    ap.add_argument("--budget", type=float, default=num_env("GCODE_BUDGET"),
                    help="stop the turn after this many USD (0 = no limit)")
    ap.add_argument("--usage", nargs="?", const=30, type=int, metavar="DAYS",
                    help="show token spend and exit")
    ap.add_argument("--models", action="store_true",
                    help="list chat models your key can reach, with prices")
    args = ap.parse_args()

    if args.usage is not None:
        show_usage(args.usage)
        return

    key = os.environ.get("OPENAI_API_KEY", "")
    if not key or key.endswith("..."):
        sys.exit(
            "OPENAI_API_KEY is not set.\n"
            "  .env files checked: %s\n"
            "  Fix: put OPENAI_API_KEY=sk-... in a .env next to gcode.py or in this folder,\n"
            "  or run:  $env:OPENAI_API_KEY = \"sk-...\"   (PowerShell, this session only)"
            % (", ".join(str(p) for p in LOADED_ENV) or "none found"))

    client = OpenAI()

    if args.models:
        skip = ("audio", "realtime", "transcribe", "tts", "search", "image", "embedding")
        for mid in sorted(m.id for m in client.models.list()):
            if not mid.startswith(("gpt-", "o3", "o4")) or any(s in mid for s in skip):
                continue
            k = price_key(mid)
            print("  %-34s %s" % (mid, "$%s/$%s per 1M in/out" % PRICES[k][::2] if k
                                  else "price unknown"))
        print("\n  Listed != usable: some models need org verification. "
              "Current default: " + DEFAULT_MODEL)
        return

    messages = [{"role": "system", "content": SYSTEM.format(
        cwd=pathlib.Path.cwd(), platform=sys.platform)}]
    spent = 0.0

    if args.prompt:
        messages.append({"role": "user", "content": " ".join(args.prompt)})
        spent += agent_turn(client, args.model, messages, args.yolo, args.budget, spent)
        print("\033[90m$%.4f this run\033[0m" % spent)
        return

    print("gcode | %s | %s" % (args.model, pathlib.Path.cwd()))
    print("/exit  /reset  /cost  /usage\n")
    while True:
        try:
            line = input("\033[1myou>\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line in ("/exit", "/quit"):
            break
        if line == "/reset":
            del messages[1:]
            print("context cleared\n")
            continue
        if line == "/cost":
            print("$%.4f this session\n" % spent)
            continue
        if line == "/usage":
            show_usage(30)
            continue

        messages.append({"role": "user", "content": line})
        try:
            spent += agent_turn(client, args.model, messages, args.yolo, args.budget, spent)
        except KeyboardInterrupt:
            print("\n[interrupted]\n")
        print("\033[90m$%.4f session\033[0m\n" % spent)

    print("\n$%.4f this session. Run `gcode --usage` for the full log." % spent)


if __name__ == "__main__":
    main()

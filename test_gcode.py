"""Self-check: python test_gcode.py  (no pytest needed)."""
import json
import os
import pathlib
import tempfile
from types import SimpleNamespace

import gcode


def usage(pin, pout, cached=0):
    return SimpleNamespace(prompt_tokens=pin, completion_tokens=pout,
                           prompt_tokens_details=SimpleNamespace(cached_tokens=cached))


def main():
    tmp = tempfile.mkdtemp()
    os.chdir(tmp)

    # write -> read -> edit round trip
    assert "wrote" in gcode.run_tool("write_file", {"path": "a/b.txt", "content": "hi\nthere"}, True)
    assert "there" in gcode.run_tool("read_file", {"path": "a/b.txt"}, True)
    assert "edited" in gcode.run_tool("edit_file",
                                      {"path": "a/b.txt", "old_string": "there",
                                       "new_string": "you"}, True)
    assert pathlib.Path("a/b.txt").read_text() == "hi\nyou"

    # edit refuses ambiguous and missing matches
    gcode.run_tool("write_file", {"path": "c.txt", "content": "x x"}, True)
    assert gcode.run_tool("edit_file", {"path": "c.txt", "old_string": "x",
                                        "new_string": "y"}, True).startswith("ERROR")
    assert gcode.run_tool("edit_file", {"path": "c.txt", "old_string": "zz",
                                        "new_string": "y"}, True).startswith("ERROR")

    # sandbox: no escaping the working directory
    assert gcode.run_tool("read_file", {"path": "../../etc/passwd"}, True).startswith("ERROR")

    # approval actually blocks when the user says no
    gcode.confirm = lambda what, yolo: False
    assert gcode.run_tool("write_file", {"path": "nope.txt", "content": "x"}, False) == "DENIED by user"
    assert not pathlib.Path("nope.txt").exists()
    gcode.confirm = lambda what, yolo: True

    # shell tool reports exit codes
    assert "exit=0" in gcode.run_tool("run_shell", {"command": "echo ok"}, True)

    # cost math: 1M in + 1M out on gpt-5.4 = 2.50 + 15.00
    assert abs(gcode.cost("gpt-5.4", usage(1_000_000, 1_000_000)) - 17.50) < 1e-6
    # longest match wins: gpt-5.4-mini is not gpt-5.4, dated snapshots resolve
    assert abs(gcode.cost("gpt-5.4-mini-2026-03-17", usage(1_000_000, 0)) - 0.75) < 1e-6
    assert abs(gcode.cost("gpt-5-mini", usage(1_000_000, 0)) - 0.25) < 1e-6
    # cached input uses the family's own cached rate, not a flat 10%
    assert abs(gcode.cost("gpt-4o", usage(1_000_000, 0, cached=1_000_000)) - 1.25) < 1e-6
    assert abs(gcode.cost("gpt-5.4", usage(1_000_000, 0, cached=1_000_000)) - 0.25) < 1e-6
    # dated snapshots resolve to their base model
    assert gcode.price_key("gpt-5-2025-08-07") == "gpt-5"
    # variants are NEVER priced from their parent: gpt-5.4-pro is 12x gpt-5.4
    assert gcode.PRICES["gpt-5.4-pro"][0] > 10 * gcode.PRICES["gpt-5.4"][0]
    assert gcode.price_key("gpt-5.4-turbo-imaginary") is None
    assert gcode.price_key("gpt-4.1-frobnicate") is None
    # unknown model costs 0 instead of crashing
    assert gcode.cost("some-future-model", usage(100, 100)) == 0.0

    # usage log is valid jsonl and totals up
    gcode.HOME = pathlib.Path(tmp) / "home"
    gcode.USAGE_LOG = gcode.HOME / "usage.jsonl"
    gcode.log_usage("gpt-5.4", usage(1_000_000, 0), "test")
    row = json.loads(gcode.USAGE_LOG.read_text().splitlines()[0])
    assert row["usd"] == 2.5 and row["in"] == 1_000_000

    # .env parsing: quotes stripped, inline comments dropped, real env wins
    env = pathlib.Path(tmp) / ".env"
    env.write_text('# comment\nGC_A="v1"\nGC_B=v2   # trailing note\nGC_C=0   # cap\n'
                   "GC_TAKEN=from_file\nbroken line\n")
    os.environ["GC_TAKEN"] = "from_shell"
    assert gcode.load_dotenv([env]) == [env]
    assert os.environ["GC_A"] == "v1"
    assert os.environ["GC_B"] == "v2"
    assert os.environ["GC_TAKEN"] == "from_shell"
    assert gcode.num_env("GC_C") == 0.0
    assert gcode.num_env("GC_B", 5) == 5  # unparseable falls back, no crash
    assert gcode.num_env("GC_MISSING", 3) == 3

    print("all checks passed")


if __name__ == "__main__":
    main()

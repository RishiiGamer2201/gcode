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

    # cost math: 1M in + 1M out on gpt-5 = 1.25 + 10.00
    assert abs(gcode.cost("gpt-5", usage(1_000_000, 1_000_000)) - 11.25) < 1e-6
    # longest prefix wins: gpt-5-mini is not gpt-5
    assert abs(gcode.cost("gpt-5-mini-2026-01-01", usage(1_000_000, 0)) - 0.25) < 1e-6
    # cached input is 10x cheaper
    assert abs(gcode.cost("gpt-5", usage(1_000_000, 0, cached=1_000_000)) - 0.125) < 1e-6
    # unknown model costs 0 instead of crashing
    assert gcode.cost("some-future-model", usage(100, 100)) == 0.0

    # usage log is valid jsonl and totals up
    gcode.HOME = pathlib.Path(tmp) / "home"
    gcode.USAGE_LOG = gcode.HOME / "usage.jsonl"
    gcode.log_usage("gpt-5", usage(1_000_000, 0), "test")
    row = json.loads(gcode.USAGE_LOG.read_text().splitlines()[0])
    assert row["usd"] == 1.25 and row["in"] == 1_000_000

    print("all checks passed")


if __name__ == "__main__":
    main()

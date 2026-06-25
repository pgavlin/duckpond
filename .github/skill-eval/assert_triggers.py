#!/usr/bin/env python3
"""Assert the ducktail skill TRIGGERS for a natural-language request.

Reads a `claude -p --output-format stream-json` transcript on stdin and checks which skills the
model chose to invoke (the `Skill` tool). The skill must be discoverable from its description
alone -- a canonical request once went straight to a generic process skill and never surfaced
duckpond. Exit non-zero if ducktail did not fire.

Usage: claude -p "<natural prompt>" --output-format stream-json --verbose | assert_triggers.py
"""
import re
import sys

data = sys.stdin.read()
# stream-json carries the Skill tool's input as {"skill":"<name>"}; collect in order of appearance.
skills = re.findall(r'"skill"\s*:\s*"([^"]+)"', data)

print(f"skills fired (in order): {skills or '(none)'}")
if not any("ducktail" in s for s in skills):
    sys.exit("FAIL: ducktail did not trigger for the natural request -- its description is not "
             "winning discovery (a generic/process skill likely grabbed the request instead)")
if "ducktail" not in skills[0]:
    print(f"NOTE: ducktail fired but not first (first was {skills[0]!r}); acceptable as long as it "
          "is in the room to inform the design")
print("TRIGGER CHECK: PASS (ducktail was selected for the natural request)")

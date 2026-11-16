"""`json.loads` with one depth limit on every Python.

Python 3.12 and 3.13 raise RecursionError on deeply nested input. 3.14 decodes as deep as the C
stack allows, so the same line is an error or a value depending on `ulimit -s`. Readers of input
they do not control (transcripts, logs, CLI output) call `loads` and treat a ValueError as bad
JSON on any version.

hooks/hook_support.py holds a copy of this guard: the hooks run as `python3 -I` scripts from the
plugin folder, where `coven` is not importable. tests/hooks/test_hook_support.py checks that the
two agree.
"""

import json
import re
from itertools import accumulate

MAX_DEPTH = 512

# A string literal, or one bracket. `findall` gives the bracket, or "" for a string, so brackets
# and escaped quotes inside strings are dropped without a Python loop over the characters.
_STRING_OR_BRACKET = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"|([\[\]{}])', re.DOTALL)
_STEP = {"[": 1, "{": 1, "]": -1, "}": -1}


def _deepest(text: str) -> int:
    brackets = "".join(_STRING_OR_BRACKET.findall(text))
    return max(accumulate(map(_STEP.__getitem__, brackets)), default=0)


def loads(text: str) -> object:
    """`json.loads(text)`, or a ValueError when it nests deeper than MAX_DEPTH."""
    if _deepest(text) > MAX_DEPTH:
        raise ValueError(f"JSON nested deeper than {MAX_DEPTH} levels")
    return json.loads(text)

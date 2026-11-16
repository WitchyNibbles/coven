"""What the hooks share: a loader for the modules next to them, a depth-limited JSON reader and a
never-failing failure log.

Hooks run as `python3 -I <hook>`, which leaves this folder off sys.path, and they are not a
package. Each hook therefore puts this folder on sys.path itself and imports this module by name
at the top, and loads its other siblings (`sibling`) by path, on first use. A missing or broken
sibling then fails only the check that needs it: deny-secrets and pm-permissions deny, and the
log, which is best effort, is skipped.

This module itself is loaded at import time, so a missing or broken one crashes the hook before
its own error handling runs. That is safe only because fail-closed.sh turns any exit but 0 and 2
into a block.
"""

import functools
import importlib.util
import json
import logging
import re
import sys
from itertools import accumulate
from pathlib import Path
from types import ModuleType

MAX_DEPTH = 512
# A string literal, or one bracket; `findall` gives "" for a string. See src/coven/safe_json.py,
# which this copies: a hook runs `python3 -I` from the plugin folder and cannot import coven. The
# closing quote is optional so that a cut-off string cannot make the scan quadratic.
_STRING_OR_BRACKET = re.compile(r'"[^"\\]*(?:\\.?[^"\\]*)*"?|([\[\]{}])', re.DOTALL)
_STEP = {"[": 1, "{": 1, "]": -1, "}": -1}


@functools.cache
def sibling(name: str) -> ModuleType:
    """The module `<name>.py` next to this file, loaded by path and registered as
    `coven_hook_<name>`. Bytecode is not written: the plugin folder stays as it was installed."""
    path = Path(__file__).resolve().with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"coven_hook_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    old, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = old
    return module


def log_failure(hook: str, error: str) -> None:
    """Record a failure of `hook` in its log. The log is best effort: this never raises, so a
    missing or broken hooklog.py cannot change what the hook answers."""
    try:
        sibling("hooklog").log_event(hook, {"error": error})
    except Exception:
        logging.exception("coven %s: could not write the hook log", hook)


def loads(text: str) -> object:
    """`json.loads(text)`, or a ValueError when it nests deeper than MAX_DEPTH. Python 3.12/3.13
    raise RecursionError on deep input and 3.14 decodes it as far as the C stack allows; this
    gives every version the same answer."""
    brackets = "".join(_STRING_OR_BRACKET.findall(text))
    if max(accumulate(map(_STEP.__getitem__, brackets)), default=0) > MAX_DEPTH:
        raise ValueError(f"JSON nested deeper than {MAX_DEPTH} levels")
    return json.loads(text)

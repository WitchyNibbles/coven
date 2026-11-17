"""Start a hook in this one interpreter: `python3 -I launch.py <hook.py> [args]`.

fail-closed.sh used to start one interpreter to ask for its version and a second to run the hook.
This file is both: it exits 86 at once, before it reads stdin, when Python is older than 3.12, and
otherwise runs the hook as `__main__`. The hook is imported by name from its own folder
(runpy.run_module, not run_path), so Python reuses and writes `__pycache__` bytecode for it and for
the modules it imports; a script's own bytecode is never cached. A hook that itself exits 86 is
turned into 2, so 86 only ever means "too old".

Written in syntax that Python 2.7 and every 3.x parse (no f-strings, no annotations, no walrus),
so that an old interpreter reaches the version check instead of failing on a syntax error.
"""

import os
import runpy
import sys

TOO_OLD = 86
BLOCK = 2
MINIMUM = (3, 12)

if sys.version_info < MINIMUM:
    sys.exit(TOO_OLD)

hook = os.path.abspath(sys.argv[1])
sys.path.insert(0, os.path.dirname(hook))
sys.argv = [hook] + sys.argv[2:]
try:
    runpy.run_module(
        os.path.splitext(os.path.basename(hook))[0], run_name="__main__", alter_sys=True
    )
except SystemExit as stop:
    if stop.code == TOO_OLD:
        sys.exit(BLOCK)
    raise

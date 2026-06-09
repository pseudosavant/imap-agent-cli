from __future__ import annotations

import asyncio
import sys


def _noop_add_signal_handler(self: object, *args: object, **kwargs: object) -> None:
    return None


# pymap registers POSIX signal handlers at startup. Windows event loops do not
# implement that API, but a child-process test server can be stopped by the test
# harness directly.
asyncio.AbstractEventLoop.add_signal_handler = _noop_add_signal_handler  # type: ignore[method-assign]
asyncio.BaseEventLoop.add_signal_handler = _noop_add_signal_handler  # type: ignore[method-assign]

from pymap.main import main


if __name__ == "__main__":
    sys.argv = ["pymap", *sys.argv[1:]]
    raise SystemExit(main())

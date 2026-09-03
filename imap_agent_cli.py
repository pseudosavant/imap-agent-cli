# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "IMAPClient>=3.0.0",
#   "beautifulsoup4>=4.12.0",
#   "bleach>=6.2.0",
#   "markdownify>=0.14.0",
#   "packaging>=23.2",
#   "PyYAML>=6.0",
# ]
# ///
"""Local development wrapper for imap-agent-cli."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
shadow = sys.modules.get("imap_agent_cli")
if shadow is not None and not hasattr(shadow, "__path__"):
    del sys.modules["imap_agent_cli"]

from imap_agent_cli.cli import main


if __name__ == "__main__":
    raise SystemExit(main())

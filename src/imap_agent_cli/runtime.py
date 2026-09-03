"""Identify installed code without depending on its launcher."""

from __future__ import annotations

import json
from importlib import metadata
from pathlib import Path
from urllib.parse import urlsplit


DISTRIBUTION_NAME = "imap-agent-cli"


def is_local_development() -> bool:
    """Skip local sources and code whose installed origin cannot be verified.

    A local wheel is a built distribution, unlike a local source archive or
    directory. Its PEP 610 archive metadata therefore remains eligible.
    """
    try:
        distribution = metadata.distribution(DISTRIBUTION_NAME)
        module = Path(__file__).resolve()
        files = distribution.files or ()
        if not any(
            str(item).replace("\\", "/") == "imap_agent_cli/runtime.py"
            and Path(distribution.locate_file(item)).resolve() == module
            for item in files
        ):
            return True
        direct_url = distribution.read_text("direct_url.json")
        if direct_url is None:
            return False
        source = json.loads(direct_url)
        url = urlsplit(source["url"])
        if "dir_info" in source:
            return True
        if url.scheme == "file":
            return not (url.path.lower().endswith(".whl") and isinstance(source.get("archive_info"), dict))
        return not bool(url.scheme)
    except (metadata.PackageNotFoundError, OSError, ValueError, KeyError, TypeError, AttributeError):
        return True

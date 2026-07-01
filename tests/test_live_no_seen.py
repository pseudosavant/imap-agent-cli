from __future__ import annotations

import os
import unittest

from tests import _bootstrap  # noqa: F401

from imap_agent_cli.config import load_config, resolve_profile
from imap_agent_cli.imap_client import ImapSession


@unittest.skipUnless(os.environ.get("IMAP_AGENT_CLI_LIVE_TEST") == "1", "set IMAP_AGENT_CLI_LIVE_TEST=1")
class LiveNoSeenTests(unittest.TestCase):
    def test_read_does_not_mark_unread_message_seen(self) -> None:
        config = load_config()
        profile = resolve_profile(config, None)
        with ImapSession(profile, config.defaults) as session:
            folder = config.defaults.default_folder
            session._select(folder, readonly=True)
            unread = list(session.server.search(["UNSEEN"]))
            if not unread:
                self.skipTest("no unread messages available for no-seen check")
            uid = int(unread[-1])
            before = session.server.fetch([uid], ["FLAGS"])
            session.read(
                folder=folder,
                uid=uid,
                body_format="metadata",
                max_body_chars=0,
                include_attachments="none",
            )
            after = session.server.fetch([uid], ["FLAGS"])
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()

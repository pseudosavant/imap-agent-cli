from __future__ import annotations

import unittest
from datetime import date

from tests import _bootstrap  # noqa: F401

from imap_agent_cli.errors import AppError
from imap_agent_cli.search import build_criteria


class SearchTests(unittest.TestCase):
    def test_builds_all_when_no_filters(self) -> None:
        self.assertEqual(
            build_criteria(
                subject=None,
                sender=None,
                recipient=None,
                message_id=None,
                text=None,
                since=None,
                before=None,
                unseen=False,
                seen=False,
                answered=False,
                flagged=False,
                larger=None,
                smaller=None,
            ),
            ["ALL"],
        )

    def test_builds_subject_sender_and_dates(self) -> None:
        criteria = build_criteria(
            subject="invoice",
            sender="sender@example.com",
            recipient="recipient@example.com",
            message_id="<abc@example.com>",
            text="contract",
            since="2026-01-01",
            before="2026-02-01",
            unseen=True,
            seen=False,
            answered=True,
            flagged=True,
            larger=100,
            smaller=200000,
        )
        self.assertEqual(
            criteria,
            [
                "SUBJECT",
                "invoice",
                "FROM",
                "sender@example.com",
                "TO",
                "recipient@example.com",
                "HEADER",
                "Message-ID",
                "<abc@example.com>",
                "TEXT",
                "contract",
                "SINCE",
                date(2026, 1, 1),
                "BEFORE",
                date(2026, 2, 1),
                "UNSEEN",
                "ANSWERED",
                "FLAGGED",
                "LARGER",
                100,
                "SMALLER",
                200000,
            ],
        )

    def test_rejects_bad_date(self) -> None:
        with self.assertRaises(AppError):
            build_criteria(
                subject=None,
                sender=None,
                recipient=None,
                message_id=None,
                text=None,
                since="01/01/2026",
                before=None,
                unseen=False,
                seen=False,
                answered=False,
                flagged=False,
                larger=None,
                smaller=None,
            )

    def test_rejects_seen_and_unseen_together(self) -> None:
        with self.assertRaises(AppError):
            build_criteria(
                subject=None,
                sender=None,
                recipient=None,
                message_id=None,
                text=None,
                since=None,
                before=None,
                unseen=True,
                seen=True,
                answered=False,
                flagged=False,
                larger=None,
                smaller=None,
            )


if __name__ == "__main__":
    unittest.main()

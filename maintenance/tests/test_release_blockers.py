from __future__ import annotations

import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "maintenance/tools/check_release_blockers.py"
SPEC = importlib.util.spec_from_file_location("check_release_blockers", SCRIPT)
assert SPEC and SPEC.loader
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


def encoded(payload: object) -> bytes:
    return json.dumps(payload).encode("utf-8")


class ReleaseBlockerCheckerTests(unittest.TestCase):
    def test_issues_and_pull_requests_are_filtered_by_exact_priority_labels(self):
        issues = [
            {"number": 10, "state": "open", "labels": [{"name": "P0"}]},
            {"number": 11, "state": "open", "labels": [{"name": "P1"}], "pull_request": {}},
            {"number": 12, "state": "open", "labels": [{"name": "security"}]},
            {"number": 13, "state": "open", "labels": [{"name": "P10"}, {"name": "P1-extra"}]},
        ]
        self.assertEqual([10], CHECKER.find_blockers(issues))

    def test_later_page_is_not_ignored(self):
        first_page = [
            {"number": index, "state": "open", "labels": []}
            for index in range(1, 101)
        ]
        second_page = [{"number": 201, "state": "open", "labels": [{"name": "P1"}]}]
        responses = {
            "https://api.github.com/repos/example/project/issues?state=open&per_page=100&page=1": encoded(first_page),
            "https://api.github.com/repos/example/project/issues?state=open&per_page=100&page=2": encoded(second_page),
        }

        def fetcher(url: str, timeout: float) -> bytes:
            del timeout
            return responses[url]

        issues = CHECKER.fetch_open_issues(
            "example/project", "token", fetcher=fetcher, deadline_seconds=5
        )
        self.assertEqual([201], CHECKER.find_blockers(issues))

    def test_pagination_limit_fails_closed(self):
        page = [{"number": index, "state": "open", "labels": []} for index in range(1, 101)]

        def fetcher(_url: str, timeout: float) -> bytes:
            del timeout
            return encoded(page)

        with self.assertRaisesRegex(CHECKER.BlockerCheckError, "paginação"):
            CHECKER.fetch_open_issues(
                "example/project", "token", fetcher=fetcher, deadline_seconds=5, max_pages=1
            )

    def test_malformed_issue_schema_fails_closed(self):
        def fetcher(_url: str, timeout: float) -> bytes:
            del timeout
            return encoded([{"number": "10", "state": "open", "labels": []}])

        with self.assertRaisesRegex(CHECKER.BlockerCheckError, "schema"):
            CHECKER.fetch_open_issues(
                "example/project", "token", fetcher=fetcher, deadline_seconds=5
            )

    def test_transport_failure_fails_closed_without_leaking_details(self):
        def fetcher(_url: str, timeout: float) -> bytes:
            del timeout
            raise RuntimeError("token=secret transport details")

        with self.assertRaisesRegex(CHECKER.BlockerCheckError, "rede") as context:
            CHECKER.fetch_open_issues(
                "example/project", "token", fetcher=fetcher, deadline_seconds=5
            )
        self.assertNotIn("secret", str(context.exception))

    def test_default_transport_uses_remote_client_limits_and_authentication(self):
        class FakeClient:
            def __init__(self):
                self.calls: list[tuple[str, dict[str, object]]] = []

            def get(self, url: str, **kwargs: object) -> bytes:
                self.calls.append((url, kwargs))
                return encoded([])

        client = FakeClient()
        self.assertEqual(
            [],
            CHECKER.fetch_open_issues(
                "example/project", "token", client=client, deadline_seconds=5
            ),
        )
        self.assertEqual(1, len(client.calls))
        _, kwargs = client.calls[0]
        self.assertEqual(CHECKER.MAX_PAGE_BYTES, kwargs["maximum_size"])
        self.assertEqual(1, kwargs["attempts"])
        self.assertIn("Bearer token", kwargs["headers"]["Authorization"])

    def test_cli_requires_token_and_returns_only_issue_numbers(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
            CHECKER, "fetch_open_issues", return_value=[
                {"number": 42, "state": "open", "labels": [{"name": "P0"}]}
            ]
        ):
            self.assertEqual(2, CHECKER.main(["--repo", "example/project"]))

        with mock.patch.object(
            CHECKER, "fetch_open_issues", return_value=[
                {"number": 42, "state": "open", "labels": [{"name": "P0"}]}
            ]
        ):
            self.assertEqual(
                1,
                CHECKER.main(["--repo", "example/project", "--token", "token"]),
            )

    def test_invalid_json_fails_closed(self):
        def fetcher(_url: str, timeout: float) -> bytes:
            del timeout
            return b"not-json"

        with self.assertRaisesRegex(CHECKER.BlockerCheckError, "JSON"):
            CHECKER.fetch_open_issues(
                "example/project", "token", fetcher=fetcher, deadline_seconds=5
            )

    def test_oversized_response_fails_closed_before_json_processing(self):
        def fetcher(_url: str, timeout: float) -> bytes:
            del timeout
            return b"x" * (CHECKER.MAX_PAGE_BYTES + 1)

        with self.assertRaisesRegex(CHECKER.BlockerCheckError, "excede o limite"):
            CHECKER.fetch_open_issues(
                "example/project", "token", fetcher=fetcher, deadline_seconds=5
            )


if __name__ == "__main__":
    unittest.main()

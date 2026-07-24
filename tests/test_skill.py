from __future__ import annotations

import importlib.util
import io
import json
import os
from contextlib import redirect_stdout
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/deploy-star-history-pages"
INSTALLER = SKILL / "scripts/install.py"
STAR_HISTORY_ASSET = SKILL / "assets/star_history.py"
WORKFLOW_ASSET = SKILL / "assets/sync-star-history.yml.tmpl"
README_SNIPPET_ASSET = SKILL / "assets/readme-snippet.html.tmpl"
PINNED_SOURCE_REF = "bcddc9d532b10bac7e0187a741288bf9cab17616"


def load_project_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


star_history = load_project_module("star_history_asset", STAR_HISTORY_ASSET)


def make_svg(repository: str, theme: str) -> bytes:
    background = "#0d1117" if theme == "dark" else "#fff"
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="533.333">'
        f"<style>svg{{background:{background};font-family:xkcd}}</style>"
        f"<desc>Star History GitHub Stars {repository} xkcdify"
        f"{'x' * 11_000}</desc></svg>"
    ).encode()


def make_data(repository: str, last_count: int = 100):
    return {
        "schema_version": 1,
        "repository": repository,
        "updated_at": "2026-07-02T00:00:00Z",
        "logo_url": "data:image/png;base64,AA==",
        "star_records": [
            {"date": "2026/7/1 0:0:0", "count": 1},
            {"date": "2026/7/2 0:0:0", "count": last_count},
        ],
    }


def write_upstream_fixture(root: Path) -> None:
    destination = root / star_history.MAIN_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        star_history.STARTUP_ORIGINAL + "\n  return true;\n};\n",
        encoding="utf-8",
    )


class InstallerTests(unittest.TestCase):
    def run_installer(
        self, root: Path, *arguments: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(INSTALLER), "--root", str(root), *arguments],
            check=check,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_install_from_github_origin_and_repeat(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "remote",
                    "add",
                    "origin",
                    "git@github.com:Example-Org/Demo-Repo.git",
                ],
                check=True,
            )

            first = self.run_installer(root, "--no-snippet")
            self.assertIn("[created] .github/scripts/star_history.py", first.stdout)
            self.assertIn(
                "[created] .github/data/star-history-data.json",
                first.stdout,
            )
            workflow = root / ".github/workflows/sync-star-history.yml"
            script = root / ".github/scripts/star_history.py"
            seed = root / ".github/data/star-history-data.json"
            self.assertTrue(workflow.is_file())
            self.assertTrue(script.is_file())
            self.assertTrue(script.stat().st_mode & 0o111)

            content = workflow.read_text()
            self.assertIn("STAR_HISTORY_REPOSITORY: example-org/demo-repo", content)
            self.assertIn(
                "STAR_HISTORY_PAGES_URL: https://example-org.github.io/demo-repo",
                content,
            )
            self.assertIn(f"STAR_HISTORY_SOURCE_REF: {PINNED_SOURCE_REF}", content)
            self.assertIn("GITHUB_JOB_TOKEN: ${{ github.token }}", content)
            self.assertNotIn("STAR_HISTORY_GITHUB_TOKEN", content)
            self.assertNotRegex(content, r"__[A-Z0-9_]+__")

            payload = json.loads(seed.read_text())
            self.assertEqual(payload["repository"], "example-org/demo-repo")
            self.assertIs(payload["bootstrap"], True)
            star_history.validate_data(payload, "example-org/demo-repo")

            second = self.run_installer(root, "--dry-run", "--no-snippet")
            self.assertEqual(second.returncode, 0)
            self.assertEqual(second.stdout.count("[would unchanged]"), 3)

    def test_conflict_requires_force(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.run_installer(root, "--repository", "octocat/demo", "--no-snippet")
            workflow = root / ".github/workflows/sync-star-history.yml"
            workflow.write_text(workflow.read_text() + "\n# local change\n")

            conflict = self.run_installer(
                root,
                "--repository",
                "octocat/demo",
                "--no-snippet",
                check=False,
            )
            self.assertEqual(conflict.returncode, 3)
            self.assertIn("[conflict]", conflict.stderr)

            forced = self.run_installer(
                root,
                "--repository",
                "octocat/demo",
                "--force",
                "--no-snippet",
            )
            self.assertIn(
                "[replaced] .github/workflows/sync-star-history.yml",
                forced.stdout,
            )
            self.assertNotIn("# local change", workflow.read_text())

    def test_legacy_renderer_is_reported_without_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / ".github/scripts/render_star_history.py"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("# customized legacy renderer\n")

            result = self.run_installer(
                root,
                "--repository",
                "octocat/demo",
                "--no-snippet",
            )
            self.assertIn("[legacy] .github/scripts/render_star_history.py", result.stdout)
            self.assertEqual(legacy.read_text(), "# customized legacy renderer\n")

    def test_user_site_and_invalid_cron(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.run_installer(
                root,
                "--repository",
                "octocat/octocat.github.io",
                "--dry-run",
                "--no-snippet",
            )
            self.assertIn("pages_url=https://octocat.github.io", result.stdout)

            invalid = self.run_installer(
                root,
                "--repository",
                "octocat/demo",
                "--cron",
                "bad cron",
                "--dry-run",
                "--no-snippet",
                check=False,
            )
            self.assertEqual(invalid.returncode, 2)
            self.assertIn("cron", invalid.stderr)


class StarHistoryAssetTests(unittest.TestCase):
    # The single metadata request must not list individual stargazers.
    def test_repository_metadata_request_and_bootstrap(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self, _limit: int = -1) -> bytes:
                return json.dumps(
                    {
                        "stargazers_count": 123,
                        "created_at": "2024-01-02T03:04:05Z",
                        "owner": {
                            "avatar_url": "https://avatars.githubusercontent.com/u/1?v=4"
                        },
                    }
                ).encode()

        with patch.object(
            star_history.urllib.request,
            "urlopen",
            return_value=FakeResponse(),
        ) as urlopen:
            metadata = star_history.fetch_repository_metadata(
                "example-org/demo-repo",
                "test-token",
            )
        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://api.github.com/repos/example-org/demo-repo",
        )
        self.assertNotIn("/stargazers", request.full_url)

        with patch.object(
            star_history,
            "download_avatar_data_url",
            return_value="data:image/png;base64,AA==",
        ):
            payload = star_history.bootstrap_history(
                repository="example-org/demo-repo",
                metadata=metadata,
                current_time=datetime(2026, 7, 24, 12, tzinfo=timezone.utc),
            )
        self.assertNotIn("bootstrap", payload)
        self.assertEqual(payload["star_records"][0]["count"], 0)
        self.assertEqual(payload["star_records"][-1]["count"], 123)

    # Pages JSON is the commit-free source of truth. Only an initial 404 may
    # use the seed; a transient failure must not overwrite accumulated history.
    def test_pages_history_preferred_and_transient_failure_stops(self) -> None:
        repository = "example-org/demo-repo"
        with tempfile.TemporaryDirectory() as directory:
            seed = Path(directory) / "seed.json"
            seed.write_text(json.dumps(make_data(repository, 100)))
            deployed = make_data(repository, 101)

            with patch.object(star_history, "download_json", return_value=deployed):
                payload, source = star_history.load_best_data(
                    repository=repository,
                    seed_file=seed,
                    deployed_url="https://example-org.github.io/demo-repo/data.json",
                )
            self.assertEqual(payload["star_records"][-1]["count"], 101)
            self.assertEqual(source, "deployed Pages data")

            with patch.object(
                star_history,
                "download_json",
                side_effect=urllib.error.HTTPError(
                    "https://example-org.github.io/demo-repo/data.json",
                    404,
                    "Not Found",
                    None,
                    io.BytesIO(b""),
                ),
            ):
                payload, source = star_history.load_best_data(
                    repository=repository,
                    seed_file=seed,
                    deployed_url="https://example-org.github.io/demo-repo/data.json",
                )
            self.assertEqual(payload["star_records"][-1]["count"], 100)
            self.assertEqual(source, "repository seed")

            with patch.object(
                star_history,
                "download_json",
                side_effect=urllib.error.URLError("offline"),
            ), self.assertRaisesRegex(RuntimeError, "preserving the last deployment"):
                star_history.load_best_data(
                    repository=repository,
                    seed_file=seed,
                    deployed_url="https://example-org.github.io/demo-repo/data.json",
                )

    # Two scheduled runs on the same UTC date must be byte-stable when the
    # total is unchanged; a changed total replaces the point and a new date
    # appends exactly one point.
    def test_current_record_coalesces_same_day_and_appends_next_day(self) -> None:
        repository = "example-org/demo-repo"
        original = make_data(repository, 100)
        unchanged = star_history.update_current_record(
            original,
            repository=repository,
            star_count=100,
            current_time=datetime(2026, 7, 2, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(unchanged, original)

        same_day = star_history.update_current_record(
            original,
            repository=repository,
            star_count=101,
            current_time=datetime(2026, 7, 2, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(len(same_day["star_records"]), 2)
        self.assertEqual(
            same_day["star_records"][-1],
            {"date": "2026/7/2 12:0:0", "count": 101},
        )

        next_day = star_history.update_current_record(
            same_day,
            repository=repository,
            star_count=102,
            current_time=datetime(2026, 7, 3, 1, 2, 3, tzinfo=timezone.utc),
        )
        self.assertEqual(len(next_day["star_records"]), 3)
        self.assertEqual(
            next_day["star_records"][-1],
            {"date": "2026/7/3 1:2:3", "count": 102},
        )

    # The backend adaptation must seed official cache data and fail closed if
    # the reviewed upstream startup fragment changes.
    def test_upstream_patch_is_guarded_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_upstream_fixture(root)
            self.assertEqual(star_history.patch_upstream(root), [star_history.MAIN_PATH])
            source = (root / star_history.MAIN_PATH).read_text()
            self.assertIn("STAR_HISTORY_DATA_PATH", source)
            self.assertIn("cache.set(repository", source)
            self.assertEqual(star_history.patch_upstream(root), [])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / star_history.MAIN_PATH
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text("const startServer = changedUpstream;\n")
            with self.assertRaisesRegex(RuntimeError, "unexpected upstream"):
                star_history.patch_upstream(root)

    # The renderer must keep both themes atomic and accept only localhost as
    # its backend origin.
    def test_light_and_dark_render_with_mock_backend(self) -> None:
        repository = "example-org/demo-repo"

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urllib.parse.urlsplit(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                self.server.requests.append(query)  # type: ignore[attr-defined]
                theme = "dark" if query.get("theme") == ["dark"] else "light"
                body = make_svg(repository, theme)
                self.send_response(200)
                self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_: object) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.requests = []  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                result = subprocess.run(
                    [
                        sys.executable,
                        str(STAR_HISTORY_ASSET),
                        "render",
                        "--backend-url",
                        f"http://127.0.0.1:{server.server_port}",
                        "--repository",
                        repository,
                        "--output-dir",
                        directory,
                    ],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                self.assertEqual(result.stdout.count("[rendered]"), 2)
                for theme in ("light", "dark"):
                    output = Path(directory) / f"star-history-{theme}.svg"
                    self.assertGreater(output.stat().st_size, 10_000)
                self.assertEqual(len(server.requests), 2)  # type: ignore[attr-defined]
        finally:
            server.shutdown()
            server.server_close()

    # A local renderer failure may reuse only a complete, validated Pages
    # pair. Actions receives one visible annotation; local runs stay free of
    # workflow control messages.
    def test_renderer_fallback_pair_and_annotation_policy(self) -> None:
        repository = "example-org/demo-repo"

        class FailingHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(503)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"renderer unavailable")

            def log_message(self, *_: object) -> None:
                pass

        class PagesHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                theme = "dark" if self.path.endswith("-dark.svg") else "light"
                body = make_svg(repository, theme)
                self.send_response(200)
                self.send_header("Content-Type", "image/svg+xml")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_: object) -> None:
                pass

        failing = ThreadingHTTPServer(("127.0.0.1", 0), FailingHandler)
        pages = ThreadingHTTPServer(("127.0.0.1", 0), PagesHandler)
        threads = [
            threading.Thread(target=failing.serve_forever, daemon=True),
            threading.Thread(target=pages.serve_forever, daemon=True),
        ]
        for thread in threads:
            thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                args = star_history.build_parser().parse_args(
                    [
                        "render",
                        "--backend-url",
                        f"http://127.0.0.1:{failing.server_port}",
                        "--repository",
                        repository,
                        "--output-dir",
                        directory,
                        "--fallback-base-url",
                        f"http://127.0.0.1:{pages.server_port}",
                    ]
                )
                output = io.StringIO()
                with patch.object(
                    star_history.time,
                    "sleep",
                    return_value=None,
                ), patch.dict(
                    os.environ,
                    {"GITHUB_ACTIONS": "true"},
                ), redirect_stdout(output):
                    self.assertEqual(star_history.render_command(args), 0)

                for theme in ("light", "dark"):
                    self.assertEqual(
                        (Path(directory) / f"star-history-{theme}.svg").read_bytes(),
                        make_svg(repository, theme),
                    )
                self.assertIn("::warning title=Star History::", output.getvalue())
        finally:
            failing.shutdown()
            pages.shutdown()
            failing.server_close()
            pages.server_close()
            for thread in threads:
                thread.join(timeout=5)

        local_output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(local_output):
            star_history.emit_github_warning("local check")
        self.assertEqual(local_output.getvalue(), "")

    # Production assets must stay independent of the hosted service and the
    # access-restricted individual-stargazer listing endpoint.
    def test_production_assets_use_no_hosted_chart_or_stargazer_listing(self) -> None:
        text = "\n".join(
            path.read_text().lower()
            for path in (
                STAR_HISTORY_ASSET,
                WORKFLOW_ASSET,
                README_SNIPPET_ASSET,
            )
        )
        for forbidden in (
            "api.star-history.com",
            "www.star-history.com",
            "/stargazers",
            "star_history_github_token",
        ):
            self.assertNotIn(forbidden, text)
        for action in (
            "actions/checkout@v7",
            "actions/setup-node@v7",
            "pnpm/action-setup@v6",
        ):
            self.assertIn(action, text)
        self.assertEqual(text.count("persist-credentials: false"), 2)
        self.assertIn("pnpm run build", text)


if __name__ == "__main__":
    unittest.main()

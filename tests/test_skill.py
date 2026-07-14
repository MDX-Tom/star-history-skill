from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/deploy-star-history-pages"
INSTALLER = SKILL / "scripts/install.py"
RENDERER = SKILL / "assets/render_star_history.py"
PINNED_SOURCE_REF = "bcddc9d532b10bac7e0187a741288bf9cab17616"


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
            self.assertIn("[created] .github/scripts/render_star_history.py", first.stdout)
            workflow = root / ".github/workflows/sync-star-history.yml"
            renderer = root / ".github/scripts/render_star_history.py"
            self.assertTrue(workflow.is_file())
            self.assertTrue(renderer.is_file())
            self.assertTrue(renderer.stat().st_mode & 0o111)

            content = workflow.read_text()
            self.assertIn("STAR_HISTORY_REPOSITORY: example-org/demo-repo", content)
            self.assertIn(
                "STAR_HISTORY_PAGES_URL: https://example-org.github.io/demo-repo",
                content,
            )
            self.assertIn(f"STAR_HISTORY_SOURCE_REF: {PINNED_SOURCE_REF}", content)
            self.assertNotRegex(content, r"__[A-Z0-9_]+__")

            second = self.run_installer(root, "--dry-run", "--no-snippet")
            self.assertEqual(second.returncode, 0)
            self.assertEqual(second.stdout.count("[would unchanged]"), 2)

    def test_conflict_requires_force(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.run_installer(
                root, "--repository", "octocat/demo", "--no-snippet"
            )
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
            self.assertIn("[replaced] .github/workflows/sync-star-history.yml", forced.stdout)
            self.assertNotIn("# local change", workflow.read_text())

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


class RendererTests(unittest.TestCase):
    def test_light_and_dark_render_with_mock_backend(self) -> None:
        repository = "example-org/demo-repo"

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urllib.parse.urlsplit(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                self.server.requests.append(query)  # type: ignore[attr-defined]
                dark = query.get("theme") == ["dark"]
                background = "#0d1117" if dark else "#fff"
                body = (
                    '<svg xmlns="http://www.w3.org/2000/svg" '
                    'width="800" height="533.333">'
                    f"<style>svg{{background:{background}}}</style>"
                    f"<text>Star History GitHub Stars {repository}</text>"
                    f"<!-- {'x' * 11000} -->"
                    "</svg>"
                ).encode()
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
                        str(RENDERER),
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


if __name__ == "__main__":
    unittest.main()

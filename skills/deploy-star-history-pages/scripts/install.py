#!/usr/bin/env python3
"""Install the self-hosted Star History GitHub Actions pipeline."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
ASSETS_DIR = SKILL_DIR / "assets"
DEFAULT_SOURCE_REF = "bcddc9d532b10bac7e0187a741288bf9cab17616"
REPOSITORY_PATTERN = re.compile(
    r"^(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?)/"
    r"(?P<repo>[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?)$"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Write a GitHub Actions workflow and renderer that publish the target "
            "repository's Star History SVGs to GitHub Pages."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Target Git repository root (default: current directory)",
    )
    parser.add_argument(
        "--repository",
        help="GitHub repository in owner/name form; inferred from origin when omitted",
    )
    parser.add_argument(
        "--pages-url",
        help="GitHub Pages origin; inferred from owner/name when omitted",
    )
    parser.add_argument(
        "--source-ref",
        default=DEFAULT_SOURCE_REF,
        help="star-history/star-history Git ref to check out (default: tested commit SHA)",
    )
    parser.add_argument(
        "--cron",
        default="23 */12 * * *",
        help="UTC cron schedule for refreshes (default: %(default)s)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace differing generated files",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the planned changes without writing files",
    )
    parser.add_argument(
        "--no-snippet",
        action="store_true",
        help="Do not print the README picture snippet",
    )
    return parser.parse_args()


def git_origin(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def repository_from_remote(remote: str) -> str | None:
    value = remote.strip()
    scp_match = re.fullmatch(r"git@github\.com:(?P<path>[^?#]+)", value)
    if scp_match:
        path = scp_match.group("path")
    else:
        parsed = urllib.parse.urlsplit(value)
        if (parsed.hostname or "").lower() != "github.com":
            return None
        path = parsed.path.lstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return path if REPOSITORY_PATTERN.fullmatch(path) else None


def normalize_repository(value: str) -> tuple[str, str, str]:
    candidate = value.strip().removesuffix(".git")
    match = REPOSITORY_PATTERN.fullmatch(candidate)
    if not match:
        raise ValueError("repository must use GitHub owner/name format")
    owner = match.group("owner").lower()
    repo = match.group("repo").lower()
    return f"{owner}/{repo}", owner, repo


def infer_pages_url(owner: str, repo: str) -> str:
    if repo == f"{owner}.github.io":
        return f"https://{owner}.github.io"
    return f"https://{owner}.github.io/{repo}"


def normalize_pages_url(value: str) -> str:
    candidate = value.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(candidate)
    if parsed.scheme != "https" or not parsed.hostname or any(
        character.isspace() for character in candidate
    ):
        raise ValueError("pages URL must be an HTTPS origin or project-site URL")
    if parsed.query or parsed.fragment or '"' in candidate or "'" in candidate:
        raise ValueError("pages URL must not contain a query or fragment")
    return candidate


def one_line(name: str, value: str) -> str:
    candidate = value.strip()
    if not candidate or "\n" in candidate or "\r" in candidate:
        raise ValueError(f"{name} must be a non-empty single line")
    return candidate


def validate_source_ref(value: str) -> str:
    candidate = one_line("source ref", value)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", candidate):
        raise ValueError("source ref contains characters that are invalid in a Git ref")
    if ".." in candidate or candidate.endswith((".", "/")) or "//" in candidate:
        raise ValueError("source ref is not a valid branch, tag, or commit name")
    return candidate


def validate_cron(value: str) -> str:
    candidate = one_line("cron", value)
    if not re.fullmatch(r"[0-9*/, -]+", candidate):
        raise ValueError("cron may contain only digits, spaces, *, /, comma, and hyphen")
    if len(candidate.split()) != 5:
        raise ValueError("cron must contain exactly five UTC fields")
    return candidate


def render_asset(name: str, replacements: dict[str, str]) -> str:
    content = (ASSETS_DIR / name).read_text(encoding="utf-8")
    for placeholder, value in replacements.items():
        content = content.replace(placeholder, value)
    leftovers = sorted(set(re.findall(r"__[A-Z0-9_]+__", content)))
    if leftovers:
        raise RuntimeError(
            f"unreplaced placeholders in {name}: {', '.join(leftovers)}"
        )
    return content


def atomic_write(path: Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=path.name + ".",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    os.chmod(temporary_path, mode)
    temporary_path.replace(path)


def status_for(path: Path, content: str) -> str:
    if not path.exists():
        return "create"
    try:
        current = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return "replace"
    return "unchanged" if current == content else "replace"


def main() -> int:
    args = parse_args()
    root = args.root.expanduser().resolve()
    if not root.is_dir():
        print(f"[error] target root is not a directory: {root}", file=sys.stderr)
        return 2

    try:
        repository_value = args.repository
        if not repository_value:
            origin = git_origin(root)
            repository_value = repository_from_remote(origin) if origin else None
        if not repository_value:
            raise ValueError(
                "could not infer a GitHub repository from origin; pass --repository owner/name"
            )

        repository, owner, repo = normalize_repository(repository_value)
        pages_url = normalize_pages_url(
            args.pages_url or infer_pages_url(owner, repo)
        )
        source_ref = validate_source_ref(args.source_ref)
        cron = validate_cron(args.cron)

        workflow = render_asset(
            "sync-star-history.yml.tmpl",
            {
                "__STAR_HISTORY_REPOSITORY__": repository,
                "__STAR_HISTORY_PAGES_URL__": pages_url,
                "__STAR_HISTORY_SOURCE_REF__": source_ref,
                "__STAR_HISTORY_CRON__": cron,
            },
        )
        renderer = (ASSETS_DIR / "render_star_history.py").read_text(
            encoding="utf-8"
        )
        snippet = render_asset(
            "readme-snippet.html.tmpl",
            {
                "__STAR_HISTORY_REPOSITORY__": repository,
                "__STAR_HISTORY_REPOSITORY_QUERY__": urllib.parse.quote(
                    repository, safe=""
                ),
                "__STAR_HISTORY_PAGES_URL__": pages_url,
            },
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2

    planned = (
        (root / ".github/scripts/render_star_history.py", renderer, 0o755),
        (root / ".github/workflows/sync-star-history.yml", workflow, 0o644),
    )
    statuses = [(path, content, mode, status_for(path, content)) for path, content, mode in planned]
    conflicts = [path for path, _, _, status in statuses if status == "replace"]
    if conflicts and not args.force:
        for path in conflicts:
            print(f"[conflict] {path}", file=sys.stderr)
        print("[error] rerun with --dry-run to inspect or --force to replace", file=sys.stderr)
        return 3

    verb = "would" if args.dry_run else "will"
    print(f"[target] repository={repository}")
    print(f"[target] pages_url={pages_url}")
    for path, content, mode, status in statuses:
        relative = path.relative_to(root)
        if args.dry_run:
            print(f"[{verb} {status}] {relative}")
        elif status == "unchanged":
            print(f"[unchanged] {relative}")
        else:
            atomic_write(path, content, mode)
            print(f"[{status}d] {relative}")

    if not args.no_snippet:
        print("\n--- README snippet ---")
        print(snippet.rstrip())
        print("--- end snippet ---")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

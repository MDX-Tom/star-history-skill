#!/usr/bin/env python3
"""Persist Star History SVGs rendered by a local official backend instance."""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional


DEFAULT_BACKEND_URL = "http://127.0.0.1:8080"
THEMES = ("light", "dark")
REPOSITORY_PATTERN = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?/"
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?$"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Save light and dark SVGs produced by a local Star History backend."
    )
    parser.add_argument(
        "--backend-url",
        default=os.environ.get("STAR_HISTORY_BACKEND_URL", DEFAULT_BACKEND_URL),
        help="Local official backend origin (default: %(default)s)",
    )
    parser.add_argument(
        "--repository",
        default=os.environ.get("STAR_HISTORY_REPOSITORY"),
        required="STAR_HISTORY_REPOSITORY" not in os.environ,
        help="GitHub repository in owner/name form",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".star-history-site"),
        help="Directory that receives the two SVG files (default: %(default)s)",
    )
    return parser.parse_args()


def validate_local_backend_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value.rstrip("/"))
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("backend URL must point to a local HTTP server")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("backend URL must be an origin without a path or query")
    return value.rstrip("/")


def validate_repository(value: str) -> str:
    repository = value.strip().lower()
    if not REPOSITORY_PATTERN.fullmatch(repository):
        raise ValueError("repository must use GitHub owner/name format")
    return repository


def chart_url(backend_url: str, repository: str, theme: str) -> str:
    params = {
        "repos": repository,
        "type": "date",
        "legend": "top-left",
    }
    if theme == "dark":
        params["theme"] = "dark"
    return f"{backend_url}/svg?{urllib.parse.urlencode(params)}"


def download_svg(url: str, attempts: int = 4) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "image/svg+xml",
            "User-Agent": "deploy-star-history-pages local renderer",
        },
    )
    last_error: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                content_type = response.headers.get("Content-Type", "").lower()
                body = response.read()
            if "svg" not in content_type:
                raise ValueError(
                    f"unexpected Content-Type: {content_type or '(missing)'}"
                )
            return body
        except urllib.error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", errors="replace").strip()
            detail = f"HTTP {exc.code} {exc.reason}"
            if response_body:
                detail += f": {response_body}"
            last_error = RuntimeError(detail)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = exc
        if attempt < attempts:
            time.sleep(attempt * 2)
    raise RuntimeError(f"local Star History backend request failed: {last_error}")


def validate_svg(content: bytes, repository: str, theme: str) -> None:
    if len(content) < 10_000:
        raise ValueError(f"SVG is unexpectedly small: {len(content)} bytes")
    root = ET.fromstring(content)
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise ValueError(f"document root is not SVG: {root.tag}")
    if not root.attrib.get("width") or not root.attrib.get("height"):
        raise ValueError("SVG is missing width or height")

    text = content.decode("utf-8")
    required_fragments = ("Star History", "GitHub Stars", repository)
    missing = [fragment for fragment in required_fragments if fragment not in text]
    if missing:
        raise ValueError(f"SVG is missing chart markers: {', '.join(missing)}")

    expected_background = "background:#0d1117" if theme == "dark" else "background:#fff"
    if expected_background not in text:
        raise ValueError(f"SVG does not contain the expected {theme} theme")


def atomic_write(destination: Path, content: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=destination.name + ".",
        suffix=".tmp",
        dir=destination.parent,
        delete=False,
    ) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    temporary_path.replace(destination)
    destination.chmod(0o644)
    print(f"[rendered] {destination} ({len(content)} bytes)")


def main() -> int:
    args = parse_args()
    try:
        backend_url = validate_local_backend_url(args.backend_url)
        repository = validate_repository(args.repository)
        for theme in THEMES:
            content = download_svg(chart_url(backend_url, repository, theme))
            validate_svg(content, repository, theme)
            atomic_write(args.output_dir / f"star-history-{theme}.svg", content)
    except (RuntimeError, OSError, UnicodeDecodeError, ValueError, ET.ParseError) as exc:
        print(f"[error] Star History rendering failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

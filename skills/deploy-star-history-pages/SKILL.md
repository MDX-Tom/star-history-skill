---
name: deploy-star-history-pages
description: Add, repair, or migrate a GitHub repository's Star History chart to a repository-owned GitHub Actions and GitHub Pages pipeline. Use when a README depends on api.star-history.com or star-history.com images, when charts are stale or unavailable, or when scheduled light/dark SVGs should use the official renderer without the hosted chart API, a PAT, individual-stargazer requests, or recurring Git commits.
---

# Deploy Star History Pages

Generate light and dark Star History SVGs with the pinned official renderer on a GitHub Actions runner, then publish the chart pair and its date/count state to the repository's own GitHub Pages site.

## Workflow

### 1. Inspect the repository

1. Read the nearest `AGENTS.md`, all maintained README languages, and existing Pages workflows.
2. Derive `owner/repository` from `git remote get-url origin`, or get it from the user.
3. Inspect existing `.github/scripts/star_history.py`, `.github/data/star-history-data.json`, and `.github/workflows/sync-star-history.yml`. Preserve intentional customizations.
4. Determine the Pages base URL:
   - Project site: `https://owner.github.io/repository`
   - User/organization site named `owner.github.io`: `https://owner.github.io`

### 2. Install the reusable files

From this skill directory, run:

```bash
python3 scripts/install.py \
  --root /path/to/target-repository \
  --repository owner/repository
```

The installer writes:

- `.github/scripts/star_history.py`
- `.github/data/star-history-data.json`
- `.github/workflows/sync-star-history.yml`

It refuses to replace different existing files unless `--force` is supplied. Use `--dry-run` before replacing an existing setup. Optional flags include `--pages-url`, `--source-ref`, and `--cron`. Keep the tested upstream commit SHA unless following upstream tip is intentional.

The committed JSON is a deterministic cold-start seed. Runtime history is published as `star-history-data.json` on Pages and read back on the next run. Do not overwrite a customized seed or delete the Pages state without understanding the reset behavior in [references/operations.md](references/operations.md).

### 3. Update every README language

Use the `<picture>` snippet printed by the installer.

- Load `star-history-dark.svg` for dark color schemes.
- Load `star-history-light.svg` for light color schemes and the `<img>` fallback.
- Point image URLs to the repository's Pages site, never the hosted chart API.
- Keep README translations and formatting synchronized.
- Do not add a hosted-service link by default. Add an optional interactive link only when the user explicitly requests it; image delivery must remain repository-owned.

### 4. Configure GitHub Pages

Set **Settings → Pages → Build and deployment → Source** to **GitHub Actions**.

No PAT or repository secret is needed. The workflow uses the job-scoped `${{ github.token }}` for one repository-metadata request and never lists individual stargazers. Do not add credentials to generated files.

Read [references/operations.md](references/operations.md) before changing state storage, permissions, the upstream pin, or fallback behavior.

### 5. Validate locally

```bash
SKILL_DIR=/absolute/path/to/deploy-star-history-pages

python3 -m py_compile .github/scripts/star_history.py
python3 "$SKILL_DIR/scripts/install.py" \
  --root . \
  --repository owner/repository \
  --dry-run
git diff --check
```

When available:

```bash
actionlint .github/workflows/sync-star-history.yml
```

Verify that generated files contain no unreplaced `__PLACEHOLDER__` values and no reference to `api.star-history.com` or `/stargazers`.

### 6. Deploy and verify

When remote deployment is requested and GitHub CLI is authenticated:

```bash
gh workflow run sync-star-history.yml --repo owner/repository
gh run watch --repo owner/repository --exit-status
curl -fsS https://owner.github.io/repository/star-history-data.json >/dev/null
curl -fsS https://owner.github.io/repository/star-history-light.svg >/dev/null
curl -fsS https://owner.github.io/repository/star-history-dark.svg >/dev/null
```

Allow a short Pages propagation delay after the first deployment. Confirm the README theme switch and inspect the chart visually.

## Implementation invariants

- Run the pinned official `star-history/star-history` backend only on `127.0.0.1` inside the runner.
- Preserve the official JSDOM, `XYChart`, xkcd styling, theme, and SVGO path; patch only the backend cache seed.
- Read the current total with one authenticated `GET /repos/{owner}/{repo}` request using `${{ github.token }}`.
- Never call the hosted Star History chart service or GitHub's individual-stargazer listing endpoint.
- Treat deployed Pages JSON as runtime history. Use the committed seed only when the Pages JSON path returns HTTP 404; fail closed on other download or validation errors.
- Coalesce multiple updates on the same UTC date and append a point on a new UTC date.
- Validate repository identity, chronology, embedded logo, SVG structure, official markers, dimensions, and theme before publication.
- Publish JSON, both SVGs, `.nojekyll`, and `manifest.sha256` as one Pages artifact.
- Skip Pages deployment when the manifest is byte-identical. The scheduled workflow never creates a Git commit.
- Keep `contents: read`, `deployments: write`, `pages: write`, and `id-token: write`; do not broaden permissions without a concrete need.
- Use a concurrency group and keep only the newest `github-pages` deployment record.
- Keep a reviewed upstream commit SHA because the workflow executes third-party source.

## Completion report

Report:

1. Target repository and Pages URL.
2. Files created or changed, including README translations.
3. Validation commands and results.
4. Whether the Pages source still needs user configuration.
5. Whether the workflow ran and all three public assets were verified.
6. Whether history was migrated from existing Pages JSON or initialized from the cold-start seed.

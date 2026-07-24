# Star History Pages operations

## Contents

1. Architecture and trust boundaries
2. History storage and lifecycle
3. GitHub Pages activation
4. Validation and rollout
5. Failure diagnosis
6. Backup, reset, and upstream maintenance

## 1. Architecture and trust boundaries

Each run checks out a pinned revision of `star-history/star-history`, installs its lockfile-pinned dependencies, and patches only `backend/main.ts` so the official backend can seed its in-memory cache from local JSON. The backend remains bound to `127.0.0.1:8080`; its JSDOM, `XYChart`, xkcd styling, theme selection, and SVGO output path remain unchanged.

The workflow makes one authenticated GitHub repository-metadata request (`GET /repos/{owner}/{repo}`) to read the current `stargazers_count`, creation timestamp, and owner avatar. It uses the ephemeral job-scoped `${{ github.token }}`. It does not require a PAT, repository secret, individual-stargazer pagination, or the hosted Star History chart API.

The Pages artifact contains:

```text
.nojekyll
manifest.sha256
star-history-data.json
star-history-light.svg
star-history-dark.svg
```

The token, upstream checkout, dependencies, backend log, and temporary files remain runner-local.

External dependencies remain: GitHub Actions, GitHub's repository metadata endpoint, the official source repository, npm/pnpm package delivery, and GitHub Pages. The previously deployed Pages artifact remains readable between runs.

## 2. History storage and lifecycle

There are two JSON locations with different roles:

| Location | Role | Updated automatically? | Creates Git commits? |
| --- | --- | --- | --- |
| `.github/data/star-history-data.json` | Deterministic bootstrap/cold-recovery seed | No | Only when a maintainer intentionally edits it |
| `https://owner.github.io/repository/star-history-data.json` | Runtime source of truth | Yes, through Pages deployment | No |

At the start of a run, the workflow downloads the Pages JSON. Only HTTP 404 selects the committed seed. Timeouts, other HTTP errors, schema errors, or a repository mismatch stop the refresh so accumulated history is not silently replaced.

The updater then reads the current total once:

- Same UTC date, unchanged total: leave JSON byte-stable and usually skip deployment.
- Same UTC date, changed total: replace that day's latest point.
- New UTC date: append one point, even when the count is unchanged.

The first run from the generated bootstrap seed creates two points: repository creation at zero and the current total. It does not reconstruct historical individual-star timestamps. For an exact migration, supply a validated existing history JSON as the seed before the first deployment or keep the existing Pages JSON reachable.

Because runtime state lives on Pages, scheduled refreshes never modify the Git branch and never create automatic commits. Pages deployments are separate repository deployment records; the workflow retains only the newest `github-pages` record.

## 3. GitHub Pages activation

UI path:

1. Open **Settings**.
2. Open **Pages**.
3. Under **Build and deployment**, choose **GitHub Actions**.

No Actions secret is required. The workflow declares:

```yaml
permissions:
  contents: read
  deployments: write
  pages: write
  id-token: write
```

`deployments: write` is used only to mark superseded Pages deployments inactive and delete old deployment records.

With authenticated GitHub CLI, inspect before changing remote state:

```bash
gh api repos/owner/repository/pages
```

Create or update Pages only when remote configuration is requested:

```bash
if gh api repos/owner/repository/pages >/dev/null 2>&1; then
  gh api --method PUT repos/owner/repository/pages -f build_type=workflow
else
  gh api --method POST repos/owner/repository/pages -f build_type=workflow
fi
```

## 4. Validation and rollout

Local checks:

```bash
python3 -m py_compile .github/scripts/star_history.py
git diff --check
grep -R '__[A-Z0-9_]\+__' .github/scripts .github/data .github/workflows && exit 1 || true
actionlint .github/workflows/sync-star-history.yml  # when installed
```

Remote rollout:

```bash
gh workflow run sync-star-history.yml --repo owner/repository
gh run list --workflow sync-star-history.yml --repo owner/repository --limit 1
gh run watch --repo owner/repository --exit-status
```

Public verification:

```bash
curl -fL https://owner.github.io/repository/star-history-data.json -o /tmp/star-history-data.json
curl -fL https://owner.github.io/repository/star-history-light.svg -o /tmp/star-history-light.svg
curl -fL https://owner.github.io/repository/star-history-dark.svg -o /tmp/star-history-dark.svg
python3 -m json.tool /tmp/star-history-data.json >/dev/null
grep -q '<svg' /tmp/star-history-light.svg
grep -q '<svg' /tmp/star-history-dark.svg
```

Also inspect both themes visually and confirm the README `<picture>` element switches correctly.

## 5. Failure diagnosis

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `GitHub token environment variable is empty` | Job token is unavailable or the environment variable was renamed | Restore `GITHUB_JOB_TOKEN: ${{ github.token }}` and `--token-env GITHUB_JOB_TOKEN` |
| `deployed Pages cache ... preserving the last deployment` | Pages JSON timed out, is invalid, or returned a non-404 error | Keep the deployed site unchanged; verify the Pages URL and JSON before rerunning |
| `unexpected upstream implementation` | The pinned `backend/main.ts` startup fragment no longer matches | Review upstream changes, update the narrow cache-seed patch, and pin the tested revision |
| Backend exits before `/healthz` | Dependency, source, cache schema, or port startup failure | Inspect the runner-local backend log and verify `STAR_HISTORY_DATA_PATH` |
| `SVG is unexpectedly small` or missing official markers | Backend returned an error document or official output changed | Inspect the response and visually review before changing validation |
| Local render warning followed by success | The run reused the last deployed SVG pair | Fix the local renderer; JSON may be newer than the visible pair until a later successful render |
| `configure-pages` or deployment fails | Pages source, workflow permissions, or environment protection is wrong | Check Pages settings, Actions policy, declared permissions, and environment rules |
| Workflow succeeds but README is stale | Wrong Pages URL, cache propagation, or README still points elsewhere | Verify all three public files and every README language |
| Every run deploys without a new day/count | Manifest content is nondeterministic or the comparison URL is wrong | Compare local/deployed `manifest.sha256` and inspect generated JSON/SVG bytes |

## 6. Backup, reset, and upstream maintenance

### Cold backup

Runtime Pages JSON is normally sufficient and requires no manual synchronization. If deletion of the Pages site is a realistic risk, periodically download the public JSON and intentionally replace `.github/data/star-history-data.json` in a reviewed maintenance commit:

```bash
curl -fL https://owner.github.io/repository/star-history-data.json \
  -o .github/data/star-history-data.json
python3 -m json.tool .github/data/star-history-data.json >/dev/null
```

This is optional. Do not use installer `--force` afterward without reviewing the seed diff, because it would restore the deterministic bootstrap template.

Deleting both the Pages state and any maintained cold backup resets future history to the two-point bootstrap shape. Existing individual-star timestamps cannot be reconstructed by this metadata-only pipeline.

### Upstream renderer

The installer defaults to reviewed revision `bcddc9d532b10bac7e0187a741288bf9cab17616`. Use `--source-ref main` only when deliberately following upstream changes.

When updating the revision:

1. Inspect `backend/main.ts`, the `/svg` route, and package manager metadata.
2. Confirm the cache-seed patch matches exactly once and is idempotent.
3. Run the official TypeScript build.
4. Render and visually compare both themes.
5. Keep the previous Pages artifact live until the new workflow succeeds.

The production design was generalized from `MDX-Tom/gpt-5.6-instruct`: Pages-hosted JSON state, one metadata request, a locally seeded official renderer, manifest-based deployment, and no recurring Git commits.

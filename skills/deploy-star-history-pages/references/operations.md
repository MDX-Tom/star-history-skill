# Star History Pages operations

## Contents

1. Architecture and trust boundaries
2. GitHub token and secret setup
3. GitHub Pages activation
4. Validation and rollout
5. Failure diagnosis
6. Upstream maintenance

## 1. Architecture and trust boundaries

The workflow checks out `star-history/star-history`, installs its pinned lockfiles, launches the official backend on `127.0.0.1:8080`, and asks that local backend to render two SVGs for the target repository. The renderer verifies the response type, XML root, declared width and height, repository marker, chart labels, and theme background before atomic writes.

Only the resulting SVGs and `.nojekyll` enter the Pages artifact. The GitHub token, backend log, upstream checkout, dependencies, and temporary comparison files remain runner-local. The workflow also replaces upstream diagnostic strings that would otherwise print token prefixes or suffixes before it starts the backend.

This removes `api.star-history.com` from the README image delivery path. It still depends on GitHub Actions, GitHub's REST API, the official Star History source repository, the npm registry during a run, and GitHub Pages for static delivery. A previously deployed Pages copy remains available between workflow runs.

## 2. GitHub token and secret setup

Use the least-privileged token that can read the target repository's public star data.

Recommended for a public repository:

1. Create a fine-grained personal access token.
2. Limit repository access to the target repository.
3. Keep repository permissions read-only; Metadata read access is sufficient for public repository metadata and stargazer reads in the normal GitHub API model.
4. Give the token a practical expiration date and rotate it before expiry.
5. Save it as the repository Actions secret `STAR_HISTORY_GITHUB_TOKEN`.

With GitHub CLI, pass the value through standard input so it does not appear in shell history:

```bash
printf '%s' "$STAR_HISTORY_GITHUB_TOKEN" | \
  gh secret set STAR_HISTORY_GITHUB_TOKEN --repo owner/repository
```

The workflow patches the official backend's startup token check to query the target repository. This supports tokens restricted to that repository instead of requiring access to the upstream `star-history/star-history` repository during validation.

## 3. GitHub Pages activation

UI path:

1. Open **Settings**.
2. Open **Pages**.
3. Under **Build and deployment**, select **GitHub Actions** as the source.

With an authenticated GitHub CLI, inspect before changing remote state:

```bash
gh api repos/owner/repository/pages
```

Create or update the Pages configuration only when the user requested remote configuration:

```bash
if gh api repos/owner/repository/pages >/dev/null 2>&1; then
  gh api --method PUT repos/owner/repository/pages -f build_type=workflow
else
  gh api --method POST repos/owner/repository/pages -f build_type=workflow
fi
```

The workflow uses the `github-pages` environment and OpenID Connect deployment, with `pages: write` and `id-token: write` permissions.

## 4. Validation and rollout

Local checks:

```bash
python3 -m py_compile .github/scripts/render_star_history.py
git diff --check
grep -R '__[A-Z0-9_]\+__' .github/scripts .github/workflows && exit 1 || true
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
curl -fL https://owner.github.io/repository/star-history-light.svg -o /tmp/star-history-light.svg
curl -fL https://owner.github.io/repository/star-history-dark.svg -o /tmp/star-history-dark.svg
grep -q '<svg' /tmp/star-history-light.svg
grep -q '<svg' /tmp/star-history-dark.svg
```

Also open both files visually and confirm the README's `<picture>` element switches with the operating-system or browser color scheme.

## 5. Failure diagnosis

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `Repository secret STAR_HISTORY_GITHUB_TOKEN is required` | Secret is missing or named differently | Create the exact repository secret and rerun |
| Backend exits before `/healthz` | Token rejected, dependencies changed, or upstream startup changed | Read the backend log printed by the workflow; verify token and upstream ref |
| `unexpected upstream token validation implementation` or `unexpected upstream token logging implementation` | `backend/token.ts` changed upstream | Inspect the token check and diagnostic messages, update the narrow patches, then pin a tested commit |
| `SVG is unexpectedly small` or missing chart markers | Backend returned an error document or official SVG structure changed | Inspect the backend log and response; relax validation only after visual review |
| `configure-pages` or deploy step fails | Pages source is not GitHub Actions, permissions are restricted, or environment protection blocks deployment | Check repository Pages settings, Actions policy, workflow permissions, and environment rules |
| Workflow succeeds but README image is stale | Wrong Pages URL, CDN cache, or README still points at hosted API | Verify public SVG URLs, wait for propagation, and inspect every README variant |
| Every scheduled run deploys unchanged charts | Byte output is nondeterministic or comparison URL is wrong | Diff generated and deployed SVGs and verify `STAR_HISTORY_PAGES_URL` |

## 6. Upstream maintenance

The installer defaults to the reviewed upstream commit `bcddc9d532b10bac7e0187a741288bf9cab17616`. This avoids executing an unreviewed moving branch while the read-only GitHub token is present. Use `--source-ref main` only when deliberately following official fixes, and expect upstream internal changes to require maintenance.

When updating the ref:

1. Inspect `backend/token.ts`, `backend/main.ts`, package manager metadata, and the `/svg` route.
2. Confirm the token patch still matches exactly once.
3. Run one light and one dark render.
4. Visually compare the output with the previous deployment.
5. Keep the old Pages assets live until the new workflow succeeds.

The original proven implementation was extracted from `MDX-Tom/gpt-5.6-instruct`, including the local renderer, explicit repository secret, local token-validation patch, changed-content check, and GitHub Pages deployment flow.

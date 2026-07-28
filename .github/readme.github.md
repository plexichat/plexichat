# GitHub Actions — Plexichat Mirror & PR Relay

This directory contains the GitHub Actions workflows that power the Plexichat
GitHub mirror at `github.com/plexichat/plexichat`. The mirror is **read-only** —
all real work happens on `gitlab.plexichat.com`.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     github.com/plexichat/plexichat               │
│                         (Read-only Mirror)                      │
│                                                                 │
│  Contributors open PRs against `dev` ──────────────┐            │
│                                                     ▼            │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐     │
│  │ pr-checks.yml │──▶│ relay-pr.yml │──▶│ gitlab.plexichat  │     │
│  │ (CI checks)   │   │ (gather meta │   │ .com MR created  │     │
│  │ lint + sec +  │   │  + push to   │   │ against dev      │     │
│  │ test + build) │   │  GitLab +    │   └──────────────────┘     │
│  └──────────────┘   │  create MR)  │                             │
│                     └──────────────┘                             │
│                                                                 │
│  ┌────────────────┐                      ┌──────────────────┐    │
│  │ mirror-sync.yml│──────────────────────│ gitlab.plexichat  │    │
│  │ (hourly sync)  │◀─────────────────────│ .com (source of  │    │
│  │ GitLab → GitHub│                      │ truth)           │    │
│  └────────────────┘                      └──────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

## Workflows

### `pr-checks.yml` — CI Checks

Mirrors the GitLab CI pipelines for both server and client.

| Job | What it runs | Mirror of |
|---|---|---|
| `server-lint` | pre-commit (ruff, pyright, bandit, detect-secrets) | GitLab `lint:pre-commit` |
| `server-security` | pip-audit + safety (dependency scanning) | GitLab `security:dependency_scanning` |
| `server-test` | `python main.py self-test` | GitLab `test:self_test` |
| `client-lint` | pre-commit (eslint, htmlhint, stylelint, vite-build) | GitLab `lint:pre-commit` |
| `client-security` | npm audit | GitLab `security:npm_audit` |
| `client-build` | Vite production build | GitLab `build:vite` |

Triggers: `pull_request_target` on PRs against `dev`.

### `relay-pr.yml` — PR Relay to GitLab

Relays a fork PR from GitHub to GitLab as a Merge Request.

**Flow:**
1. `gather-metadata` — captures PR metadata via GitHub API (no secrets)
2. `relay-to-gitlab` — gated by `gitlab-relay` environment (approval required for first-timers)
3. `report-status` — comments on the GitHub PR with success/failure

Triggers: `workflow_run` on `PR Checks` completion (only if CI passed).

### `mirror-sync.yml` — Mirror Sync

Keeps the GitHub mirror in sync with GitLab.

- Fetches `dev` and `master` from GitLab
- Force-pushes to GitHub (mirror is read-only on GitHub)
- Syncs all version tags

Triggers: Every hour (cron) + manual dispatch.

## Setup Instructions

### 1. GitHub Secrets

Add these in **GitHub → Settings → Secrets and variables → Actions**:

| Secret | Scope | Used by |
|---|---|---|
| `GITLAB_TOKEN` | `write_repository` (push to `contrib/*`) + `api` (create MR) | `relay-pr.yml` |
| `GITLAB_MIRROR_TOKEN` | `read_repository` only | `mirror-sync.yml` |
| `GITLAB_PROJECT_ID` | N/A (numeric project ID or `group%2Fproject`) | `relay-pr.yml` |

### 2. GitHub Environment

Create an environment in **GitHub → Settings → Environments**:

- **Name:** `gitlab-relay`
- **Deployment protection rules:** Require approval for first-time contributors
- **Required reviewers:** Add maintainers

This gates the `relay-pr.yml`'s `relay-to-gitlab` job — first-time contributors
can't trigger a GitLab MR without a maintainer approving the deploy.

### 3. GitLab Access Tokens

Create these in **GitLab → Settings → Access Tokens**:

#### `GITLAB_TOKEN` (least-privilege: push contrib/*, create MR)

```
Name: github-relay
Scopes: api, write_repository
Roles: Developer (or custom with limited branch protection)
```

A custom role that can:
- Push to branches matching `contrib/*`
- Create merge requests targeting `dev`
- CANNOT push to `dev`, `master`, or protected branches

#### `GITLAB_MIRROR_TOKEN` (read-only)

```
Name: github-mirror
Scopes: read_repository
```

### 4. Branch Protection (GitHub)

In **GitHub → Settings → Branches**:

- Protect `dev` and `master`
- Require status checks to pass before merging: `PR Checks`
- Do NOT allow direct pushes to `dev` or `master`

## Security

1. **No untrusted code in relay workflow.** `relay-pr.yml` never checks out or
   executes fork code — it only reads structured GitHub API data.

2. **Two separate GitLab tokens** enforce least-privilege: one for PR relay,
   one for mirror sync (read-only). Tags are managed exclusively on GitLab.

3. **Environment gating.** First-time contributors' PRs are paused until a
   maintainer approves the `gitlab-relay` deployment.

4. **JSON injection prevention.** The GitLab MR payload is built with
   `jq --arg`, not string interpolation. PR bodies are passed via artifacts,
   not through `$GITHUB_OUTPUT`.

5. **See `pr-checks.yml` header** for the security tradeoff discussion around
   running CI on fork code with `pull_request_target`.

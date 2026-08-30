# GitHub workflows for the Plexichat mirror

This directory contains the GitHub Actions workflows that power the read-only mirror at `github.com/plexichat/plexichat`. The source of truth is `gitlab.plexichat.com`.

## Before you begin

You need maintainer access to configure secrets and environments. Contributors do not need extra setup to open pull requests.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     github.com/plexichat/plexichat               │
│                         (read-only mirror)                      │
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

### `pr-checks.yml` — CI checks

This workflow mirrors the GitLab CI pipelines for server and client. It runs on `pull_request_target` against `dev`.

| Job | Command | GitLab counterpart |
|---|---|---|
| `server-lint` | `pre-commit` with `ruff`, `pyright`, `bandit`, `detect-secrets` | `lint:pre-commit` |
| `server-security` | `pip-audit` and `safety` | `security:dependency_scanning` |
| `server-test` | `python main.py self-test` | `test:self_test` |
| `client-lint` | `pre-commit` with `eslint`, `htmlhint`, `stylelint`, `vite build` | `lint:pre-commit` |
| `client-security` | `npm audit` | `security:npm_audit` |
| `client-build` | Vite production build | `build:vite` |

### `relay-pr.yml` — Relay a pull request to GitLab

This workflow creates a GitLab merge request from a GitHub pull request. It runs only after `pr-checks.yml` succeeds.

1. Gather metadata from the GitHub API (no secrets).
2. Push to GitLab and create the merge request (requires `gitlab-relay` environment approval for first-time contributors).
3. Report the result as a comment on the GitHub pull request.

Trigger: `workflow_run` on `PR Checks` completion.

### `mirror-sync.yml` — Sync GitLab to GitHub

This workflow keeps the mirror up to date. It fetches `dev` and `master` plus tags from GitLab and force-pushes to GitHub.

Triggers: hourly cron and manual dispatch.

## Set up the mirror

Complete these steps in order.

### 1. Add GitHub secrets

Go to **GitHub → Settings → Secrets and variables → Actions** and add:

| Secret | Scope | Used by |
|---|---|---|
| `GITLAB_TOKEN` | `write_repository` (push to `contrib/*`) and `api` (create MR) | `relay-pr.yml` |
| `GITLAB_MIRROR_TOKEN` | `read_repository` only | `mirror-sync.yml` |
| `GITLAB_PROJECT_ID` | Numeric ID or `group%2Fproject` | `relay-pr.yml` |

### 2. Create the `gitlab-relay` environment

1. Go to **GitHub → Settings → Environments**.
2. Create an environment named `gitlab-relay`.
3. Add deployment protection rules and required reviewers so that first-time contributors need maintainer approval before the relay job runs.

### 3. Create GitLab access tokens

Create these in **GitLab → Settings → Access Tokens**:

**`GITLAB_TOKEN` (for `relay-pr.yml`):**

```
Name: github-relay
Scopes: api, write_repository
Role: Developer (or a custom role limited to contrib/*)
```

This token can push to `contrib/*` and create merge requests against `dev`. It cannot push to `dev`, `master`, or other protected branches.

**`GITLAB_MIRROR_TOKEN` (for `mirror-sync.yml`):**

```
Name: github-mirror
Scopes: read_repository
```

### 4. Configure branch protection

1. Go to **GitHub → Settings → Branches**.
2. Protect `dev` and `master`.
3. Require the `PR Checks` status checks.
4. Block direct pushes to `dev` and `master`.

## Security considerations

* The relay workflow never checks out or executes fork code. It reads only structured GitHub API data.
* Two separate tokens enforce least privilege: a read-only token for mirroring and a scoped token for relaying.
* The `gitlab-relay` environment gates first-time contributors behind maintainer approval.
* JSON payloads for the GitLab API use `jq --arg` instead of string interpolation, and pull request bodies travel via artifacts rather than `GITHUB_OUTPUT`.
* `pr-checks.yml` uses `pull_request_target`. Review its header comment for the trade-off discussion before you change trigger types.

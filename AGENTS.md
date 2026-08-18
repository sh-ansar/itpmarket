# Spyon repository rules

These rules apply to the whole repository.

## Source-control workflow

- `production` is the deployable branch. Start new work from an up-to-date, clean `production` branch and use a dedicated feature branch.
- Never merge into or push `production` without explicit user approval after the feature branch has been reviewed and tested.
- Never force-push `production` (or any shared branch). Production updates are fast-forward only, and the server tracked worktree must be clean.
- Preserve unrelated local changes. Do not use destructive Git commands (`reset --hard`, broad `clean`, forced checkout) to make a worktree look clean.
- Before implementation, inspect startup paths, storage selection, collectors, tests, deployment scripts, and existing documentation. Report material risks before changing behavior.

## Production contract

- The active production checkout is `C:\Spyon\current` on Windows Server 2016 and must remain on branch `production`.
- Production deployment is owned by `C:\Spyon\deploy-production.ps1` and Scheduled Task `Spyon Auto Deploy`. The deploy flow is `git fetch origin production` followed by `git merge --ff-only origin/production`, restart of `Spyon Production`, then checks of `/health`, `/ready`, and `/`.
- Do not manually copy source files into production, change the production checkout, run a database bootstrap/migration, restart tasks, or alter Caddy unless the user explicitly authorizes that production action.
- The legacy manual GitHub Actions workflow targeting `C:\ITPMarket` is not the current production deployment mechanism.
- Waitress must bind to `127.0.0.1`; Caddy is the only public listener. Production requires PostgreSQL and a valid secret-bearing `.runtime\production.env`.

## Data and secrets

- Never reset, clean, replace, or delete production databases, `.runtime`, `.venv`, browser profiles, `data`, `logs`, `output`, or `backups` as part of normal development or deployment.
- Never commit `.env`, `.runtime\production.env`, `DATABASE_URL`, session/master keys, cookies, credentials, browser profiles, databases, logs, exports, backups, or Playwright browser binaries.
- Diagnostics must redact secrets and be read-only unless a command is clearly documented as an installer or initializer and the user explicitly requested it.

## Runtime and verification

- Supported Python versions are 3.10 and 3.11. Keep `requirements.txt` and `requirements-postgres.txt` aligned with all import-time dependencies.
- Keep Playwright browsers in repository-local `.playwright` and browser/session profiles in their existing ignored locations.
- A partial, blocked, interrupted, or failed collector run must return a non-zero process exit code so Task Manager and the UI cannot report false success.
- Before handoff, run the full Python test suite, Python compilation, `pip check`, JavaScript syntax checks, PowerShell parsing, the read-only runtime diagnostic, and a local HTTP startup smoke test when the environment allows it.
- Update `docs/FUNCTIONAL_CAPABILITIES.md`, `docs/LOCAL_DEVELOPMENT.md`, and `docs/DEPLOYMENT.md` when a change alters capabilities, prerequisites, startup, storage, integrations, or deployment.

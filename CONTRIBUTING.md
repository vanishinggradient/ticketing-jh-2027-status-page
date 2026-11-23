# Contributing

This repo publishes the Japan Habba 2027 ticket status page. It runs from
encrypted state and a GitHub Actions workflow. `main` is protected, so every
change lands through a pull request.

## Before you start

- Open an issue or comment on an existing one describing the change.
- Branch from `main`. Do not commit data files or credentials.

## Local checks

```sh
python -m venv .venv
.venv/bin/pip install -r cron/requirements.txt pytest
.venv/bin/python -m pytest -q
```

The test suite must pass before a pull request is merged. CI runs the same
command on every pull request.

## Pull requests

- Keep one pull request per change and describe why it is needed.
- Changes under `.github/` and `cron/` need the owner's review, so expect a
  request for changes if you touch the workflow.
- Never commit secrets or plaintext data. `status.json`, `history.json`,
  `report.json` and `dump/` are generated and ignored.
- Do not edit `status.enc` or `history.enc` by hand. CI writes them to the
  `state` branch from the running monitor.

## Data and credentials

The live data is encrypted with AES-256-GCM before it is committed. Credentials
live in the protected `production` environment and are only available to runs
from `main`.

If you need a credential to test something, ask the owner. Do not place one in
the repository, in a workflow file, or in a pull request comment.

## Reporting a security problem

Email the owner directly instead of opening a public issue.

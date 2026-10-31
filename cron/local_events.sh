#!/usr/bin/env bash
# Local Instagram events job, run by the systemd user timer jh-status-events
# (09:00 + 17:00). Instagram rate-limits and logs out sessions used from GitHub
# Actions IPs, so scraping runs from home instead: pull latest, decrypt
# events.enc, scrape @japanhabba, re-encrypt, push. Works in its own clone so
# it never touches the working copy; CI never rewrites events.enc, so the two
# pushers don't conflict.
#
# Reads (never prints): ~/.config/jh-status-page/credentials (page login),
# .env in the working copy (INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD,
# GITHUB_PAT_TOKEN). Instagram session kept in ~/.config/jh-status-page/ig-session.
# Logs: journalctl --user -u jh-status-events
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"   # working copy: .venv and .env live here
CLONE="${XDG_DATA_HOME:-$HOME/.local/share}/jh-status-events/repo"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}/jh-status-page"
REMOTE="https://github.com/vanishinggradient/ticketing-jh-2027-status-page.git"
PY="$SRC/.venv/bin/python"

exec 9>"${XDG_RUNTIME_DIR:-/tmp}/jh-status-events.lock"
flock -n 9 || { echo "already running"; exit 0; }

env_get() { sed -n "s/^$1=//p" "$SRC/.env" | tail -1 | sed -E "s/^[\"']//; s/[\"']\$//"; }
cred_get() { sed -n "s/^$1=//p" "$CONF/credentials"; }

export PAGE_USERNAME PAGE_PASSWORD INSTAGRAM_USERNAME INSTAGRAM_PASSWORD
PAGE_USERNAME="$(cred_get username)"
PAGE_PASSWORD="$(cred_get password)"
INSTAGRAM_USERNAME="$(env_get INSTAGRAM_USERNAME)"
INSTAGRAM_PASSWORD="$(env_get INSTAGRAM_PASSWORD)"
export INSTAGRAM_SESSION_FILE="$CONF/ig-session"

auth="AUTHORIZATION: basic $(printf 'x-access-token:%s' "$(env_get GITHUB_PAT_TOKEN)" | base64 -w0)"
git_auth() { git -c credential.helper= -c "http.https://github.com/.extraheader=$auth" "$@"; }

[ -d "$CLONE/.git" ] || git_auth clone -q "$REMOTE" "$CLONE"
cd "$CLONE"
trap 'rm -f "$CLONE/events.json"' EXIT   # no plaintext left on disk
git config user.name "Ticket Monitor"
git config user.email "ticketing@japanhabba.org"

for attempt in 1 2 3; do
  git_auth fetch -q origin main
  git checkout -q -f -B main origin/main   # dedicated clone: always start from remote
  "$PY" cron/vault.py decrypt events.json
  "$PY" cron/scrape_ig.py
  "$PY" cron/vault.py encrypt events.json
  if git diff --quiet -- events.enc; then
    echo "no new events"
    exit 0
  fi
  git add events.enc
  git commit -q -m "events: $(date +%Y-%m-%d_%H:%M)"
  if git_auth push -q origin main; then
    echo "pushed events.enc"
    exit 0
  fi
  echo "push rejected (attempt $attempt), retrying"
done
exit 1

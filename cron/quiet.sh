#!/usr/bin/env bash
# Runs a cron script without leaking ticket data into Actions logs, which are
# public on a public repo. Full output stays in a temp file on the runner;
# only error lines are echoed, and numbers on ABORT lines (prices, capacity)
# are masked. Exit code is the script's own.
log="$(mktemp)"
"$@" >"$log" 2>&1
rc=$?
grep -iE 'error|fail|traceback|abort|expired' "$log" \
  | grep -viE 'sold|left|remaining' \
  | sed -E '/ABORT/s/[0-9]+/#/g'
rm -f "$log"
exit $rc

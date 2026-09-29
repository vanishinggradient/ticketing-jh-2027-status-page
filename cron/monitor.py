"""
monitor.py
==========
Hourly ticket monitoring for Japan Habba 2027.

SAFETY RULES (from ADR-042, ADR-057):
  1. This script NEVER deletes tickets. Ever. No DELETE calls.
  2. This script NEVER modifies prices, capacities, or descriptions.
  3. The ONLY write operations are: hide a ticket, unhide a ticket.
  4. Writes are DISABLED by default. Set ENABLE_PHASE_FLIP=true to enable.
  5. Every write is logged with ticket ID, name, and action.
  6. Dry-run mode prints what WOULD happen without doing it.

Approved by: Nivi Pal (WhatsApp, Sep 29 2026, "O" + "Nice")
ADR reference: ADR-058 (auto-phase activation on sellout)

Env vars:
  KONFHUB_EVENT_ID       — KonfHub event UUID (required)
  KONFHUB_REFRESH_TOKEN  — Cognito refresh token for auth (required)
  KONFHUB_TOKEN          — Bearer token (optional, auto-refreshed if missing)
  ENABLE_PHASE_FLIP      — Set to "true" to allow write operations (default: false)
  GITHUB_TOKEN           — Auto-provided by GitHub Actions
"""

import json
import os
import pathlib
import sys
from datetime import datetime, timezone, timedelta

import requests

# ── Config ───────────────────────────────────────────────────────────────────

EVENT_ID = os.environ.get("KONFHUB_EVENT_ID", "")
API_BASE = "https://api.konfhub.com"
TOKEN = os.environ.get("KONFHUB_TOKEN", "")
WRITES_ENABLED = os.environ.get("ENABLE_PHASE_FLIP", "false").lower() == "true"

# Cognito auto-refresh (eliminates manual token refresh)
# Refresh tokens last 30+ days. Stored as KONFHUB_REFRESH_TOKEN secret.
COGNITO_REGION = "ap-south-1"
COGNITO_CLIENT_ID = "5jf10rk2c0muftotp1iu2ucup0"
REFRESH_TOKEN = os.environ.get("KONFHUB_REFRESH_TOKEN", "")

# TEST_MODE: when true, phase flip ONLY operates on tickets named "TEST-*"
# When false (live mode), operates on real exhibition tickets.
# Default: test. Set MONITOR_MODE=live in GitHub Secrets to go live.
TEST_MODE = os.environ.get("MONITOR_MODE", "test").lower() != "live"

IST = timezone(timedelta(hours=5, minutes=30))

# Fields that MUST NOT be modified. The script only touches hidden_ticket.
IMMUTABLE_FIELDS = {
    "ticket_price", "no_of_tickets", "ticket_name", "description",
    "start_timestamp", "end_timestamp",
}

# Fields KonfHub rejects in PUT payload (read-only on their end)
READ_ONLY_FIELDS = {
    "count_availability", "enable_seat_selection", "forms", "is_add_on",
    "is_deleted", "linkedin_share_id", "remaining_count", "ticket_date",
    "ticket_id", "ticket_order", "tickets_sold", "time_availability",
}

# Phase order: when current sells out, activate next
PHASE_ORDER_LIVE = [
    ("Early Bird", "Phase 1"),
    ("Phase 1", "Phase 2"),
    ("Phase 2", "Phase 3"),
    ("Phase 3", "Final Phase"),
]

# Test mode phase order: only TEST-prefixed tickets
PHASE_ORDER_TEST = [
    ("TEST-EB", "TEST-P1"),
]

# Scarcity thresholds for logging (KonfHub auto-shows below 10)
SCARCITY_THRESHOLDS = [50, 25]


# ── SAFETY: Allowed write operations ─────────────────────────────────────────
#
# This is the COMPLETE list of what this script can write.
# If it's not in this list, the script cannot do it.
#
ALLOWED_WRITE_OPS = {"hide_ticket", "unhide_ticket"}
#
# NOT ALLOWED (enforced by code, not just convention):
#   - delete_ticket
#   - modify_price
#   - modify_capacity
#   - modify_description
#   - create_ticket
#   - create_coupon
#   - delete_coupon
# ─────────────────────────────────────────────────────────────────────────────


def log(msg: str):
    ts = datetime.now(IST).strftime("%H:%M:%S IST")
    print(f"[{ts}] {msg}")


# ── API helpers ──────────────────────────────────────────────────────────────

def refresh_token_via_cognito() -> str | None:
    """Get a fresh ID token using the Cognito refresh token.
    Refresh tokens last 30+ days. No email/password needed.
    """
    if not REFRESH_TOKEN:
        log("No KONFHUB_REFRESH_TOKEN set. Cannot auto-refresh.")
        return None

    cognito_url = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com"
    payload = {
        "AuthFlow": "REFRESH_TOKEN_AUTH",
        "ClientId": COGNITO_CLIENT_ID,
        "AuthParameters": {
            "REFRESH_TOKEN": REFRESH_TOKEN,
        },
    }
    headers = {
        "Content-Type": "application/x-amz-json-1.1",
        "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
    }

    try:
        resp = requests.post(cognito_url, json=payload, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            id_token = data.get("AuthenticationResult", {}).get("IdToken")
            if id_token:
                log("Token refreshed via Cognito refresh token.")
                return f"Bearer {id_token}"
        log(f"Cognito refresh failed: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        log(f"Cognito refresh error: {e}")
    return None


def api_get(endpoint: str) -> dict | None:
    global TOKEN
    headers = {"Authorization": TOKEN, "Accept": "application/json"}
    try:
        resp = requests.get(f"{API_BASE}{endpoint}", headers=headers, timeout=30)
    except requests.RequestException as e:
        log(f"GET failed: {e}")
        return None

    if resp.status_code == 401:
        log("AUTH EXPIRED (401). Attempting auto-refresh...")
        new_token = refresh_token_via_cognito()
        if new_token:
            TOKEN = new_token
            headers["Authorization"] = TOKEN
            try:
                resp = requests.get(f"{API_BASE}{endpoint}", headers=headers, timeout=30)
                if resp.status_code < 400:
                    return resp.json()
            except Exception:
                pass

        log("Auto-refresh failed. Creating GitHub Issue.")
        create_github_issue(
            "KonfHub token expired, auto-refresh failed",
            "Auto-refresh via Cognito failed. Manual refresh needed.\n\n"
            "1. Check KONFHUB_REFRESH_TOKEN secret is valid\n"
            "2. Or manually update KONFHUB_TOKEN from browser DevTools\n"
        )
        return None

    if resp.status_code >= 400:
        log(f"GET error: {resp.status_code}")
        return None

    return resp.json()


def safe_put_visibility(ticket: dict, hide: bool) -> bool:
    """
    The ONLY write operation this script performs.
    Changes hidden_ticket to True or False. Nothing else.

    Safety checks:
      1. WRITES_ENABLED must be true
      2. Only hidden_ticket field changes
      3. Price, capacity, name, description are preserved exactly
      4. No DELETE calls ever
    """
    op = "hide_ticket" if hide else "unhide_ticket"
    assert op in ALLOWED_WRITE_OPS, f"Operation {op} not in allowed list"

    tid = ticket.get("ticket_id")
    name = ticket.get("ticket_name", "?")

    if not WRITES_ENABLED:
        log(f"  DRY RUN: would {op} '{name}' (id={tid})")
        return False

    # Build payload: copy everything except read-only fields
    payload = {k: v for k, v in ticket.items() if k not in READ_ONLY_FIELDS}

    # SAFETY: verify immutable fields are unchanged
    for field in IMMUTABLE_FIELDS:
        if field in payload:
            original = ticket.get(field)
            if payload[field] != original:
                log(f"  ABORT: {field} would change from {original} to {payload[field]}")
                return False

    # The ONE change we make
    payload["hidden_ticket"] = hide

    # API requirement
    if payload.get("ticket_applicability") is None:
        payload["ticket_applicability"] = []

    headers = {
        "Authorization": TOKEN,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    endpoint = f"{API_BASE}/event/{EVENT_ID}/tickets/{tid}"
    action = "HIDE" if hide else "UNHIDE"

    try:
        resp = requests.put(endpoint, headers=headers, data=json.dumps(payload), timeout=30)
    except requests.RequestException as e:
        log(f"  PUT failed for {name}: {e}")
        return False

    if resp.status_code < 400:
        log(f"  {action}: {name} (id={tid}) OK")
        return True
    else:
        log(f"  {action} FAILED: {name}, {resp.status_code} {resp.text[:200]}")
        return False


def create_github_issue(title: str, body: str):
    gh_token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not gh_token or not repo:
        log("Cannot create issue (no GITHUB_TOKEN)")
        return
    try:
        resp = requests.post(
            f"https://api.github.com/repos/{repo}/issues",
            headers={"Authorization": f"Bearer {gh_token}", "Accept": "application/vnd.github+json"},
            json={"title": title, "body": body},
            timeout=15,
        )
        if resp.status_code < 300:
            log(f"GitHub Issue: {resp.json().get('html_url', '?')}")
    except Exception:
        pass


# ── Ticket parsing ───────────────────────────────────────────────────────────

def extract_all_tickets(data: dict) -> list:
    all_t = []
    for key in ("tickets", "data", "categorized", "uncategorized"):
        val = data.get(key)
        if val is None:
            continue
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    if "ticket_id" in item:
                        all_t.append(item)
                    elif "tickets" in item and isinstance(item["tickets"], list):
                        for t in item["tickets"]:
                            if isinstance(t, dict):
                                all_t.append(t)
    return all_t


def find_ticket(tickets: list, pattern: str) -> dict | None:
    matches = [t for t in tickets if pattern in (t.get("ticket_name") or "")]
    return matches[0] if len(matches) == 1 else None


# ── Phase flip (the only write logic) ────────────────────────────────────────

def check_and_flip_phases(tickets: list) -> list[str]:
    """
    If a phase is sold out (remaining = 0 or sold_out flag) and the next phase
    is hidden, hide the current phase and unhide the next one.

    TEST_MODE (default): only operates on tickets named TEST-*
    LIVE MODE: only operates on real exhibition tickets (Exhibition Day 1/2/Both Days)

    Only touches: hidden_ticket field. Nothing else.
    """
    actions = []

    if TEST_MODE:
        log("MODE: TEST (only TEST-* tickets)")
        for current_name, next_name in PHASE_ORDER_TEST:
            current = find_ticket(tickets, current_name)
            next_t = find_ticket(tickets, next_name)

            if not current or not next_t:
                log(f"  Test tickets not found: {current_name}={current is not None}, {next_name}={next_t is not None}")
                continue

            remaining = current.get("remaining_count", 999)
            current_hidden = current.get("hidden_ticket", False)
            next_hidden = next_t.get("hidden_ticket", True)
            sold_out = current.get("sold_out", False) or remaining == 0

            if sold_out and not current_hidden and next_hidden:
                log(f"PHASE FLIP: {current_name} sold out")
                if safe_put_visibility(current, hide=True):
                    actions.append(f"Hidden: {current_name}")
                if safe_put_visibility(next_t, hide=False):
                    actions.append(f"Unhidden: {next_name}")
            elif not sold_out:
                log(f"  {current_name}: not sold out (remaining={remaining})")
            elif current_hidden:
                log(f"  {current_name}: already hidden")
            elif not next_hidden:
                log(f"  {next_name}: already visible")
        return actions

    # Live mode: real exhibition tickets
    log("MODE: LIVE (real tickets)")
    for current_name, next_name in PHASE_ORDER_LIVE:
        for day_label in ["Day 1", "Day 2", "Both Days"]:
            current = find_ticket(tickets, f"{day_label}: {current_name}")
            next_t = find_ticket(tickets, f"{day_label}: {next_name}")

            if not current or not next_t:
                continue

            remaining = current.get("remaining_count", 999)
            current_hidden = current.get("hidden_ticket", False)
            next_hidden = next_t.get("hidden_ticket", True)

            sold_out = current.get("sold_out", False) or remaining == 0

            if sold_out and not current_hidden and next_hidden:
                log(f"PHASE FLIP: {day_label} {current_name} sold out")

                if safe_put_visibility(current, hide=True):
                    actions.append(f"Hidden: {day_label}: {current_name}")

                if safe_put_visibility(next_t, hide=False):
                    actions.append(f"Unhidden: {day_label}: {next_name}")

    return actions


# ── Status page ──────────────────────────────────────────────────────────────

def generate_status_json(tickets: list) -> dict:
    """JSON for the public status page HTML.
    ONLY includes visible (non-hidden) tickets.
    Hidden ticket data never leaves this output.
    """
    now = datetime.now(IST).strftime("%Y-%m-%d %I:%M %p IST")
    items = []
    for t in sorted(tickets, key=lambda x: x.get("ticket_order", 999)):
        name = t.get("ticket_name", "")
        if "Meet" in name and "Greet" in name:
            continue
        if t.get("hidden_ticket", False):
            continue
        items.append({
            "name": name,
            "price": t.get("ticket_price", 0),
            "capacity": t.get("no_of_tickets", 0),
            "sold": t.get("tickets_sold", 0),
            "remaining": t.get("remaining_count", 0),
        })
    return {"updated_at": now, "tickets": items}


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    global TOKEN

    if not EVENT_ID:
        log("ERROR: KONFHUB_EVENT_ID not set")
        sys.exit(1)

    if not TOKEN and REFRESH_TOKEN:
        log("KONFHUB_TOKEN not set. Attempting Cognito refresh...")
        TOKEN = refresh_token_via_cognito()
    if not TOKEN:
        log("ERROR: No KONFHUB_TOKEN and no valid refresh token. Cannot authenticate.")
        sys.exit(1)

    log(f"Monitor starting. Event: {EVENT_ID[:8]}...")
    log(f"Writes: {'ENABLED' if WRITES_ENABLED else 'DISABLED (read-only)'}")

    print()

    # Step 1: Read all tickets (GET only)
    data = api_get(f"/event/{EVENT_ID}/tickets")
    if data is None:
        sys.exit(1)

    tickets = extract_all_tickets(data)
    if not tickets:
        data = api_get(f"/event/{EVENT_ID}/ticket")
        if data:
            tickets = extract_all_tickets(data)

    if not tickets:
        log("No tickets found")
        sys.exit(1)

    log(f"Found {len(tickets)} tickets")

    total_sold = sum(t.get("tickets_sold", 0) for t in tickets)
    log(f"Total sold: {total_sold}")
    print()

    # Step 2: Check phases and flip if needed
    actions = check_and_flip_phases(tickets)

    # Step 3: Log scarcity warnings
    for t in tickets:
        name = t.get("ticket_name", "")
        remaining = t.get("remaining_count")
        hidden = t.get("hidden_ticket", True)
        if hidden or remaining is None:
            continue
        for threshold in SCARCITY_THRESHOLDS:
            if 0 < remaining <= threshold:
                log(f"SCARCITY: {name}, {remaining} left")
                break
        if remaining == 0:
            log(f"SOLD OUT: {name}")

    # Step 4: Generate status.json (repo root, where index.html reads it)
    # No status.md in the public repo: it would expose hidden ticket data.
    # Full ticket state lives in the private repo snapshots.
    repo_root = pathlib.Path(__file__).parent.parent

    status_json = generate_status_json(tickets)
    with open(repo_root / "status.json", "w") as f:
        json.dump(status_json, f, indent=2)

    log("status.json updated")

    # Step 5: Print full state
    print()
    for t in sorted(tickets, key=lambda x: x.get("ticket_order", 999)):
        name = t.get("ticket_name", "")
        left = t.get("remaining_count", "?")
        sold = t.get("tickets_sold", 0)
        cap = t.get("no_of_tickets", "?")
        h = "hidden" if t.get("hidden_ticket") else "visible"
        print(f"  {name}: {sold}/{cap} sold, {left} left [{h}]")


if __name__ == "__main__":
    main()

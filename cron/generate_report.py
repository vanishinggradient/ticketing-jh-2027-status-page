"""
generate_report.py
==================
Builds the JH 2027 vs JH 2025 sales pace tables for the team Google Sheet.
Maintains history.json (one entry per day). Outputs report.json, which
cron/update_sheet.py writes to the sheet.

Runs after monitor.py in the GitHub Actions workflow.
No API calls. Reads status.json, writes history.json + report.json.
report.json is plaintext and gitignored; it never leaves the runner.
Event annotations come from the sheet's hand-edited Events Log tab, which
update_sheet.py reads; this script only supplies the daily totals for them.
"""

import json
import pathlib
import statistics
from datetime import date, datetime, timezone, timedelta

# ── Constants ─────────────────────────────────────────────────────────────────

EVENT_DATE = date(2027, 1, 9)
IST = timezone(timedelta(hours=5, minutes=30))
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# JH 2025 historical data from Nivi's spreadsheet (Japan Habba is now biennial,
# so JH 2025 is the previous edition).
# Tuple: (days_to_jan9, cumulative_sold, date_label, note)
JH_2025 = [
    (63,  0,     "Nov 7",  ""),
    (62,  1000,  "Nov 8",  "Early Bird Sold Out"),
    (59,  1200,  "Nov 11", ""),
    (49,  1500,  "Nov 21", ""),
    (36,  2000,  "Dec 4",  ""),
    (18,  2358,  "Dec 22", ""),
    (17,  2412,  "Dec 23", ""),
    (16,  2465,  "Dec 24", ""),
    (15,  2689,  "Dec 25", "Skibidi uncles"),
    (14,  3201,  "Dec 26", ""),
    (13,  3447,  "Dec 27", ""),
    (12,  3675,  "Dec 28", ""),
    (11,  3884,  "Dec 29", ""),
    (10,  4054,  "Dec 30", ""),
    (9,   4309,  "Dec 31", ""),
    (8,   4620,  "Jan 1",  ""),
    (7,   5002,  "Jan 2",  ""),
    (6,   5300,  "Jan 3",  ""),
    (5,   5663,  "Jan 4",  "Weeb Central Post"),
    (4,   6277,  "Jan 5",  ""),
    (3,   6805,  "Jan 6",  "Gurukiran reel, JP interview"),
    (2,   7309,  "Jan 7",  ""),
    (1,   8059,  "Jan 8",  ""),
    (0,   9850,  "Jan 9",  "Price raised to Rs. 750 at night"),
    (-1,  11500, "Jan 10", ""),
    (-2,  12100, "Jan 11", "Sold out"),
]

# Pace milestones for JH 2027.
# (label, milestone_date, days_to_event, target_sold)
PACE_MILESTONES = [
    ("Nov 1, 2026",  date(2026, 11, 1),  69,  3000),
    ("Dec 1, 2026",  date(2026, 12, 1),  39,  6500),
    ("Jan 1, 2027",  date(2027, 1, 1),   8,   12000),
    ("Jan 9, 2027",  date(2027, 1, 9),   0,   18000),
]

# A day is a spike when its sales are at least SPIKE_MULTIPLIER x the median of
# the previous SPIKE_WINDOW days with data, and at least SPIKE_MIN tickets.
# Median, not mean, so one past spike doesn't hide the next.
SPIKE_MULTIPLIER = 2
SPIKE_MIN = 20
SPIKE_WINDOW = 7


# ── Helpers ───────────────────────────────────────────────────────────────────

def today_ist() -> date:
    return datetime.now(IST).date()


def days_remaining(from_date: date = None) -> int:
    return (EVENT_DATE - (from_date or today_ist())).days


def interpolate_target(d: int) -> int | None:
    """Linear interpolation between milestone targets, given days_to_event."""
    anchors = [(100, 0), (69, 3000), (39, 6500), (8, 12000), (0, 18000)]
    for i in range(len(anchors) - 1):
        d1, t1 = anchors[i]
        d2, t2 = anchors[i + 1]
        if d2 <= d <= d1:
            frac = (d1 - d) / (d1 - d2)
            return round(t1 + frac * (t2 - t1))
    return 18000 if d < 0 else None


def load_json(path: pathlib.Path, default):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return default


def save_json(path: pathlib.Path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def sold_between(totals: dict[int, int], from_dte: int, to_dte: int):
    """Tickets sold from end of day from_dte to end of day to_dte ("" if either is missing)."""
    if from_dte in totals and to_dte in totals:
        return totals[to_dte] - totals[from_dte]
    return ""


def spike_days(totals: dict[int, int]) -> set[int]:
    """Days whose sales jump well above recent days. Days with a gap before
    them have no daily number, so they can never be flagged."""
    daily = {d: totals[d] - totals[d + 1] for d in totals if d + 1 in totals}
    spikes = set()
    for d in sorted(daily, reverse=True):  # far to near
        prior = [daily[p] for p in sorted(daily, reverse=True) if p > d][-SPIKE_WINDOW:]
        if len(prior) < 3:
            continue
        if daily[d] >= SPIKE_MIN and daily[d] >= SPIKE_MULTIPLIER * statistics.median(prior):
            spikes.add(d)
    return spikes


def rag_label(pct: float) -> str:
    if pct >= 0.9:
        return "On Track"
    if pct >= 0.7:
        return "At Risk"
    return "Off Track"


# ── Tab builders (plain values; formatting lives in the sheet) ────────────────

def daily_totals(history: list) -> dict[int, int]:
    return {e["days_to_event"]: e["total_sold"] for e in history}


def pace_chart_columns(history: list) -> dict[str, list]:
    """Pace Chart: one row per day from D-100 to D+2, keyed by header text.
    update_sheet.py matches these to the sheet's own headers, since the team's
    tab has its own layout and chart."""
    jh2025 = {d: s for d, s, _, _ in JH_2025}
    jh2025_notes = {d: note for d, _, _, note in JH_2025 if note}
    jh2027 = daily_totals(history)
    spikes = spike_days(jh2027)

    days = list(range(100, -3, -1))
    target = [interpolate_target(d) for d in days]
    return {
        "Days to Jan 9": days,
        "JH 2025 (sold)": [jh2025.get(d, "") for d in days],
        "JH 2027 (sold)": [jh2027.get(d, "") for d in days],
        "Target (pace)": ["" if t is None else t for t in target],
        "Event (JH 2025)": [jh2025_notes.get(d, "") for d in days],
        # Daily sales make announcement spikes visible; the cumulative line hides them.
        "JH 2027 (sold that day)": [sold_between(jh2027, d + 1, d) for d in days],
        "Spike": ["▲ spike" if d in spikes else "" for d in days],
    }


def targets_rows(history: list) -> list[list]:
    """Pace Targets: milestone, target, actual, delta, status."""
    rows = [["Milestone", "Date", "Days to Event", "Target", "Actual", "Delta", "Status"]]
    today = today_ist()
    sold_by_date = {e["date"]: e["total_sold"] for e in history}
    current_sold = history[-1]["total_sold"] if history else 0

    for label, m_date, dte, target in PACE_MILESTONES:
        row = [label, m_date.strftime("%b %d, %Y"), dte, target]
        if m_date > today:
            # Not yet reached: show running total so far
            row += [current_sold, "", "Upcoming"]
        else:
            actual = sold_by_date.get(m_date.isoformat())
            if actual is None and history:
                actual = min(history, key=lambda e: abs(e["days_to_event"] - dte))["total_sold"]
            if actual is None:
                row += ["No data", "", ""]
            else:
                row += [actual, actual - target, rag_label(actual / target if target else 0)]
        rows.append(row)
    return rows


def breakdown_rows(status: dict) -> list[list]:
    """Live Breakdown: current visible-ticket state from status.json."""
    rows = [[f"Last updated: {status.get('updated_at', 'unknown')}"],
            ["Ticket", "Price (Rs.)", "Sold", "Capacity", "Remaining", "% Full"]]
    for t in status.get("tickets", []):
        cap = t.get("capacity", 0) or 0
        sold = t.get("sold", 0) or 0
        rows.append([t.get("name", ""), t.get("price", 0), sold, cap,
                     t.get("remaining", 0) or 0, round(sold / cap * 100, 1) if cap else 0])
    return rows


def jh2025_raw_rows() -> list[list]:
    """JH 2025 Raw: the original spreadsheet data for reference."""
    rows = [["Date", "Days to Jan 9", "Cumulative Sold", "Daily Change", "Note"]]
    prev = 0
    for d, sold, date_label, note in JH_2025:
        delta = sold - prev
        prev = sold
        rows.append([date_label, d, sold, f"+{delta:,}" if delta > 0 else "", note])
    return rows


def build_report(history: list, status: dict) -> dict:
    totals = daily_totals(history)
    return {"totals": {str(k): v for k, v in totals.items()},
            "spikes": sorted(spike_days(totals)), "tabs": [
        {"name": "Pace Chart", "mode": "columns", "columns": pace_chart_columns(history)},
        {"name": "Pace Targets", "mode": "replace", "rows": targets_rows(history)},
        {"name": "Live Breakdown", "mode": "replace", "rows": breakdown_rows(status)},
        {"name": "JH 2025 Raw", "mode": "replace", "rows": jh2025_raw_rows()},
    ]}


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("generate_report.py starting...")

    status = load_json(REPO_ROOT / "status.json", {"tickets": [], "updated_at": "unknown"})
    total_sold = sum(t.get("sold", 0) for t in status.get("tickets", []))
    dte = days_remaining()
    today = today_ist().isoformat()

    print(f"  Total sold today: {total_sold}")
    print(f"  Days to event: {dte}")

    # Update history.json
    history = load_json(REPO_ROOT / "history.json", [])
    updated = False
    for entry in history:
        if entry["date"] == today:
            entry["total_sold"] = total_sold
            entry["days_to_event"] = dte
            updated = True
            break
    if not updated:
        history.append({"date": today, "days_to_event": dte, "total_sold": total_sold})
        history.sort(key=lambda x: x["date"])

    save_json(REPO_ROOT / "history.json", history)
    print(f"  History updated: {len(history)} entries")

    save_json(REPO_ROOT / "report.json", build_report(history, status))
    print("  Saved: report.json")
    print("Done.")


if __name__ == "__main__":
    main()

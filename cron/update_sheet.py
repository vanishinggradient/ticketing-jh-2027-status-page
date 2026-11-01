"""
update_sheet.py
===============
Writes report.json (from generate_report.py) into the team Google Sheet.

Runs last in the GitHub Actions workflow, after the data is committed and the
page deployed, so a sheet failure turns the run red without losing data.

Auth: Workload Identity Federation via google-github-actions/auth, which
impersonates a service account that has no project roles and no keys. Its
only access is Editor on this one sheet (shared to its email by hand).

Env vars:
  SHEET_ID : spreadsheet key (repo secret, kept out of the public repo)

Tabs:
  "columns" mode (Pace Chart): each column is written under the header with
    the same text, so the team's own layout and chart keep working. Missing
    headers are added after the last used column.
  "replace" mode (everything else): the tab's values are cleared and rewritten.
  Events Log is hand-edited by the team: columns A:B (Date, Label) are input
    and never overwritten; C:E (days to event, sold that day, sold next 3 days)
    are computed. Labels also land in Pace Chart's "Event (JH 2027)" column.
Charts and formatting are never touched. Prints counts only, never values.
"""

import json
import os
import pathlib
import sys

from datetime import date

import google.auth
import gspread
from gspread.utils import rowcol_to_a1

from generate_report import EVENT_DATE, sold_between

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
EVENTS_TAB = "Events Log"
EVENTS_HEADER = ["Date", "Label", "Days to Event", "Sold that day", "Sold next 3 days"]


def col_letter(col: int) -> str:
    return rowcol_to_a1(1, col)[:-1]


def _norm(header) -> str:
    return " ".join(str(header).split()).lower()


def column_plan(tab: str, header_row: list, columns: dict[str, list],
                max_rows: int) -> tuple[list[str], list[dict]]:
    """Clear ranges + value ranges for a header-matched tab."""
    index = {_norm(h): i for i, h in enumerate(header_row, 1) if str(h).strip()}
    next_col = len(header_row) + 1
    clears, data = [], []
    for header, values in columns.items():
        col = index.get(_norm(header))
        if col is None:
            col, next_col = next_col, next_col + 1
            data.append({"range": f"'{tab}'!{col_letter(col)}1", "values": [[header]]})
        letter = col_letter(col)
        clears.append(f"'{tab}'!{letter}2:{letter}{max_rows}")
        data.append({"range": f"'{tab}'!{letter}2:{letter}{len(values) + 1}",
                     "values": [[v] for v in values]})
    return clears, data


def replace_plan(tab: str, rows: list[list], max_rows: int, max_cols: int) -> tuple[list[str], list[dict]]:
    """Clear ranges + value ranges for a fully rewritten tab."""
    return ([f"'{tab}'!A1:{col_letter(max_cols)}{max_rows}"],
            [{"range": f"'{tab}'!A1", "values": rows}])


def parse_event_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def events_input(rows: list[list]) -> list[list]:
    """Date/Label rows (no header) from the Events Log tab. Converts the old
    CI-written layout (Date | Days | Category | Label | URL) once."""
    if not rows:
        return []
    header, body = rows[0], rows[1:]
    if len(header) > 2 and str(header[2]).strip() == "Category":
        return [[r[0] if r else "", r[3] if len(r) > 3 else ""] for r in body]
    return [(r + ["", ""])[:2] for r in body]


def events_plan(events: list[list], totals: dict[int, int], migrate: bool,
                max_rows: int) -> tuple[list[str], list[dict]]:
    """Header (+ A:B only when migrating) and computed C:E for the Events Log."""
    t = f"'{EVENTS_TAB}'"
    computed = []
    for d, _label in events:
        ev_date = parse_event_date(d)
        if ev_date is None:
            computed.append(["", "", ""])
            continue
        dte = (EVENT_DATE - ev_date).days
        computed.append([dte, sold_between(totals, dte + 1, dte), sold_between(totals, dte, dte - 3)])
    if migrate:
        clears = [f"{t}!A1:G{max_rows}"]
        data = [{"range": f"{t}!A1", "values": [EVENTS_HEADER] + [e + c for e, c in zip(events, computed)]}]
    else:
        clears = [f"{t}!C2:E{max_rows}"]
        data = [{"range": f"{t}!C1:E1", "values": [EVENTS_HEADER[2:]]},
                {"range": f"{t}!A1:B1", "values": [EVENTS_HEADER[:2]]}]
        if computed:
            data.append({"range": f"{t}!C2:E{len(computed) + 1}", "values": computed})
    return clears, data


def event_column(events: list[list], days: list[int]) -> list[str]:
    by_day: dict[int, list[str]] = {}
    for d, label in events:
        ev_date = parse_event_date(d)
        if ev_date and str(label).strip():
            by_day.setdefault((EVENT_DATE - ev_date).days, []).append(str(label).strip())
    return [", ".join(by_day.get(d, [])) for d in days]


def main():
    sheet_id = os.environ.get("SHEET_ID", "")
    if not sheet_id:
        sys.exit("update_sheet: error: SHEET_ID not set")
    report = json.loads((REPO_ROOT / "report.json").read_text())

    creds, _ = google.auth.default(scopes=SCOPES)
    sh = gspread.authorize(creds).open_by_key(sheet_id)
    worksheets = {ws.title: ws for ws in sh.worksheets()}
    totals = {int(k): v for k, v in report["totals"].items()}

    events_ws = worksheets.get(EVENTS_TAB) or sh.add_worksheet(EVENTS_TAB, rows=200, cols=8)
    raw = events_ws.get_values("A1:E")
    migrate = bool(raw) and len(raw[0]) > 2 and str(raw[0][2]).strip() == "Category"
    events = events_input(raw)
    clears, data = events_plan(events, totals, migrate, events_ws.row_count)
    pace = next(t for t in report["tabs"] if t["name"] == "Pace Chart")
    pace["columns"]["Event (JH 2027)"] = event_column(events, pace["columns"]["Days to Jan 9"])

    for tab in report["tabs"]:
        name = tab["name"]
        rows_needed = len(tab.get("rows") or next(iter(tab.get("columns", {}).values()), [])) + 2
        ws = worksheets.get(name) or sh.add_worksheet(name, rows=max(rows_needed, 200), cols=12)
        if ws.row_count < rows_needed:
            ws.add_rows(rows_needed - ws.row_count)
        if tab["mode"] == "columns":
            header_row = ws.row_values(1)
            needed_cols = len(header_row) + len(tab["columns"])
            if ws.col_count < needed_cols:
                ws.add_cols(needed_cols - ws.col_count)
            c, d = column_plan(name, header_row, tab["columns"], ws.row_count)
        else:
            c, d = replace_plan(name, tab["rows"], ws.row_count, ws.col_count)
        clears += c
        data += d

    sh.values_batch_clear(body={"ranges": clears})
    sh.values_batch_update(body={"valueInputOption": "RAW", "data": data})
    valid = sum(1 for d, _ in events if parse_event_date(d))
    print(f"update_sheet: wrote {len(data)} ranges across {len(report['tabs']) + 1} tabs; "
          f"{valid} events read, {len(events) - valid} rows skipped")


if __name__ == "__main__":
    main()

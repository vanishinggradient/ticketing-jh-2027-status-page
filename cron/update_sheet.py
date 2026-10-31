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
Charts and formatting are never touched. Prints counts only, never values.
"""

import json
import os
import pathlib
import sys

import google.auth
import gspread
from gspread.utils import rowcol_to_a1

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


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


def main():
    sheet_id = os.environ.get("SHEET_ID", "")
    if not sheet_id:
        sys.exit("update_sheet: error: SHEET_ID not set")
    report = json.loads((REPO_ROOT / "report.json").read_text())

    creds, _ = google.auth.default(scopes=SCOPES)
    sh = gspread.authorize(creds).open_by_key(sheet_id)
    worksheets = {ws.title: ws for ws in sh.worksheets()}

    clears, data = [], []
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
    print(f"update_sheet: wrote {len(data)} ranges across {len(report['tabs'])} tabs")


if __name__ == "__main__":
    main()

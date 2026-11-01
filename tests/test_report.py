import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "cron"))
import generate_report as gr  # noqa: E402
import update_sheet as us  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
HISTORY = [
    {"date": "2026-10-01", "days_to_event": 100, "total_sold": 814},
    {"date": "2026-10-02", "days_to_event": 99, "total_sold": 952},
    {"date": "2026-10-04", "days_to_event": 97, "total_sold": 1086},
    {"date": "2026-10-05", "days_to_event": 96, "total_sold": 1114},
    {"date": "2026-10-31", "days_to_event": 70, "total_sold": 1720},
]
TOTALS = gr.daily_totals(HISTORY)


def test_pace_chart_columns():
    cols = gr.pace_chart_columns(HISTORY)
    assert "JH 2025 (sold)" in cols and not any("2026" in h for h in cols)
    days = cols["Days to Jan 9"]
    assert days[0] == 100 and days[-1] == -2 and len(days) == 103
    sold = dict(zip(days, cols["JH 2027 (sold)"]))
    assert sold[100] == 814 and sold[70] == 1720 and sold[98] == ""
    assert "Event (JH 2027)" not in cols  # filled by update_sheet from the sheet


def test_daily_sales_blank_across_gaps():
    daily = dict(zip(*[gr.pace_chart_columns(HISTORY)[k]
                       for k in ("Days to Jan 9", "JH 2027 (sold that day)")]))
    assert daily[99] == 138       # 952 - 814
    assert daily[96] == 28        # 1114 - 1086
    assert daily[97] == ""        # day 98 missing, so no honest daily number
    assert daily[100] == ""       # no day 101


def test_events_input_migrates_old_ci_layout():
    old = [["Date", "Days to Event", "Category", "Label", "Sold that day"],
           ["2026-10-04", 97, "SPEAKER/ARTIST", "Asaka announced", ""]]
    assert us.events_input(old) == [["2026-10-04", "Asaka announced"]]
    assert us.events_input([["Date", "Label"], ["2026-10-31", "test"], ["bad"]]) == [
        ["2026-10-31", "test"], ["bad", ""]]


def test_events_plan_writes_only_computed_columns():
    events = [["2026-10-02", "Reel"], ["not a date", "x"]]
    clears, data = us.events_plan(events, TOTALS, migrate=False, max_rows=200)
    assert clears == ["'Events Log'!C2:E200"]
    ranges = {d["range"]: d["values"] for d in data}
    assert ranges["'Events Log'!C2:E3"] == [[99, 138, 162], ["", "", ""]]  # 1114 - 952 over days 99 to 96
    assert not any(r.startswith("'Events Log'!A2") for r in ranges)  # input never overwritten


def test_events_plan_migration_keeps_events():
    clears, data = us.events_plan([["2026-10-04", "Asaka announced"]], TOTALS, migrate=True, max_rows=200)
    assert clears == ["'Events Log'!A1:G200"]
    assert data[0]["values"][1][:3] == ["2026-10-04", "Asaka announced", 97]


def test_event_column_joins_same_day():
    col = us.event_column([["2026-10-04", "Asaka"], ["2026-10-04", "Reel"], ["x", "skip"]], [98, 97, 96])
    assert col == ["", "Asaka, Reel", ""]


def test_report_tabs():
    report = gr.build_report(HISTORY, {"tickets": [], "updated_at": "x"})
    assert [t["name"] for t in report["tabs"]] == ["Pace Chart", "Pace Targets", "Live Breakdown", "JH 2025 Raw"]
    assert report["totals"]["70"] == 1720


def test_column_plan_matches_existing_headers():
    header_row = ["Days to Jan 9", "JH 2025 (sold)", "jh 2027  (sold)", "", "Target (pace)"]
    cols = {"Days to Jan 9": [100, 99], "Target (pace)": [0, 97], "Event (JH 2027)": ["a", ""]}
    clears, data = us.column_plan("Pace Chart", header_row, cols, 1000)
    assert "'Pace Chart'!A2:A1000" in clears and "'Pace Chart'!E2:E1000" in clears
    ranges = {d["range"]: d["values"] for d in data}
    assert ranges["'Pace Chart'!E2:E3"] == [[0], [97]]
    assert ranges["'Pace Chart'!F1"] == [["Event (JH 2027)"]]      # missing header appended
    assert ranges["'Pace Chart'!F2:F3"] == [["a"], [""]]


def test_replace_plan():
    clears, data = us.replace_plan("Events Log", [["Date"], ["2026-10-04"]], 200, 12)
    assert clears == ["'Events Log'!A1:L200"]
    assert data == [{"range": "'Events Log'!A1", "values": [["Date"], ["2026-10-04"]]}]


def test_no_openpyxl_left():
    for path in (REPO_ROOT / "cron").glob("*.py"):
        assert "openpyxl" not in path.read_text(), path.name

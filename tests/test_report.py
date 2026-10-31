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
EVENTS = [{"date": "2026-10-04", "category": "SPEAKER/ARTIST", "label": "Asaka announced",
           "url": "https://www.instagram.com/p/DeEQDj6P4FK/"}]


def test_pace_chart_columns():
    cols = gr.pace_chart_columns(HISTORY, EVENTS)
    assert "JH 2025 (sold)" in cols and not any("2026" in h for h in cols)
    days = cols["Days to Jan 9"]
    assert days[0] == 100 and days[-1] == -2 and len(days) == 103
    sold = dict(zip(days, cols["JH 2027 (sold)"]))
    assert sold[100] == 814 and sold[70] == 1720 and sold[98] == ""
    assert dict(zip(days, cols["Event (JH 2027)"]))[97] == "Asaka announced"


def test_daily_sales_blank_across_gaps():
    daily = dict(zip(*[gr.pace_chart_columns(HISTORY, [])[k]
                       for k in ("Days to Jan 9", "JH 2027 (sold that day)")]))
    assert daily[99] == 138       # 952 - 814
    assert daily[96] == 28        # 1114 - 1086
    assert daily[97] == ""        # day 98 missing, so no honest daily number
    assert daily[100] == ""       # no day 101


def test_events_log_has_sales_context():
    rows = gr.events_rows(EVENTS, HISTORY)
    assert rows[0][4:6] == ["Sold that day", "Sold next 3 days"]
    assert rows[1][1] == 97 and rows[1][4] == "" and rows[1][5] == ""  # gaps around Oct 4


def test_report_tabs():
    report = gr.build_report(HISTORY, EVENTS, {"tickets": [], "updated_at": "x"})
    assert [t["name"] for t in report["tabs"]] == [
        "Pace Chart", "Pace Targets", "Live Breakdown", "JH 2025 Raw", "Events Log"]


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

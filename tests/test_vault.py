import pathlib
import subprocess
import sys

import pytest
from cryptography.exceptions import InvalidTag

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "cron"))
import vault  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = b'{"tickets": [{"name": "Day 1", "sold": 271}]}'


def test_round_trip():
    env = vault.encrypt(DATA, "ops", "correct horse battery")
    assert vault.decrypt(env, "ops", "correct horse battery") == DATA


def test_username_is_case_and_space_insensitive():
    env = vault.encrypt(DATA, "Ops", "pw-1234567890abcdef")
    assert vault.decrypt(env, "  ops ", "pw-1234567890abcdef") == DATA


def test_wrong_password_fails():
    env = vault.encrypt(DATA, "ops", "correct horse battery")
    with pytest.raises(InvalidTag):
        vault.decrypt(env, "ops", "wrong")


def test_wrong_username_fails():
    env = vault.encrypt(DATA, "ops", "correct horse battery")
    with pytest.raises(InvalidTag):
        vault.decrypt(env, "admin", "correct horse battery")


def test_fresh_salt_and_iv_each_time():
    a = vault.encrypt(DATA, "ops", "pw")
    b = vault.encrypt(DATA, "ops", "pw")
    assert a["salt"] != b["salt"]
    assert a["iv"] != b["iv"]
    assert a["ct"] != b["ct"]


def test_envelope_has_no_plaintext():
    env = vault.encrypt(DATA, "ops", "pw")
    assert env["iter"] == 600_000
    assert b"271" not in str(env).encode()


def test_no_plaintext_data_tracked_in_git():
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    leaked = [f for f in tracked if f in vault.DATA_FILES or f.endswith((".xlsx", ".md"))]
    assert leaked == []


def _quiet(script: str):
    return subprocess.run(
        ["bash", str(REPO_ROOT / "cron" / "quiet.sh"), sys.executable, "-c", script],
        capture_output=True, text=True,
    )


def test_quiet_hides_ticket_data():
    r = _quiet("print('Total sold: 1720'); print('  Day 1: 89/300 sold, 211 left [hidden]')")
    assert r.returncode == 0
    assert r.stdout == ""


def test_quiet_shows_errors_and_keeps_exit_code():
    r = _quiet("print('GET error: 401'); print('Found 31 tickets'); raise SystemExit(3)")
    assert r.returncode == 3
    assert r.stdout.strip() == "GET error: 401"


def test_quiet_masks_numbers_on_abort():
    r = _quiet("print('  ABORT: price would change from 599 to 0')")
    assert "599" not in r.stdout
    assert "ABORT" in r.stdout


@pytest.fixture
def tmp_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(vault, "REPO_ROOT", tmp_path)
    (tmp_path / "status.json").write_bytes(b'{"a": 1}')
    (tmp_path / "events.json").write_bytes(b"[]")
    return tmp_path


def test_encrypt_keeps_ciphertext_when_unchanged(tmp_repo):
    vault.encrypt_files(["events.json"], "ops", "pw")
    before = (tmp_repo / "events.enc").read_text()
    vault.encrypt_files(["events.json"], "ops", "pw")
    assert (tmp_repo / "events.enc").read_text() == before


def test_encrypt_rewrites_when_changed(tmp_repo):
    vault.encrypt_files(["events.json"], "ops", "pw")
    before = (tmp_repo / "events.enc").read_text()
    (tmp_repo / "events.json").write_bytes(b'[{"label": "x"}]')
    vault.encrypt_files(["events.json"], "ops", "pw")
    assert (tmp_repo / "events.enc").read_text() != before


def test_encrypt_only_named_files(tmp_repo):
    vault.encrypt_files(["events.json"], "ops", "pw")
    assert (tmp_repo / "events.enc").exists()
    assert not (tmp_repo / "status.enc").exists()


def test_ig_label_and_classify():
    import scrape_ig
    caption = "\nIntroducing our fifth performer for Habba Live: Asaka, anime song singer!\n#japanhabba"
    category = scrape_ig.classify(caption)
    assert category == "SPEAKER/ARTIST"
    label = scrape_ig.make_label(category, caption)
    assert label.startswith("SPEAKER/ARTIST: Introducing our fifth performer")
    assert label.endswith("…") and len(label) <= len("SPEAKER/ARTIST: ") + 51
    assert scrape_ig.make_label("TICKETS", "") == "TICKETS"

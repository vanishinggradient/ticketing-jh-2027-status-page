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

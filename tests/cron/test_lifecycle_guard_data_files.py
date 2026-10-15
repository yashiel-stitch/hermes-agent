"""An oversized SQLite file named from an executed Python script is data, not an unscannable script."""

import stat

from cron.lifecycle_guard import scan_gateway_lifecycle

_OVER_CAP = 1024 * 1024 + 64


def _executable_python_script(tmp_path, referenced):
    script = tmp_path / "dump.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        f"DB = os.path.expanduser({str(referenced)!r})\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def _oversized(path, header: bytes) -> None:
    path.write_bytes(header + b"x" * _OVER_CAP)
    assert path.stat().st_size > 1024 * 1024


def test_large_sqlite_file_referenced_by_python_script_is_allowed(tmp_path):
    db = tmp_path / "data.db"
    _oversized(db, b"SQLite format 3\x00")
    script = _executable_python_script(tmp_path, db)

    assert scan_gateway_lifecycle(str(script)) == (False, None)


def test_large_plain_text_file_referenced_by_python_script_fails_closed(tmp_path):
    blob = tmp_path / "notes.txt"
    _oversized(blob, b"plain text\n")
    script = _executable_python_script(tmp_path, blob)

    unsafe, refusal = scan_gateway_lifecycle(str(script))

    assert unsafe is True
    assert refusal is not None
    assert str(blob) in refusal
    assert "scan cap" in refusal

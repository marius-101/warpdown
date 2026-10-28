import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
import release_notes  # noqa: E402


@pytest.mark.parametrize("subject, expected", [
    ("0.8.8: the Add Download window checks links", "The Add Download window checks links"),
    ("v1.2.0: fix", "Fix"),
    ("0.8.7", None),
    ("CI smoke test: show the connector log", "CI smoke test: show the connector log"),
    ("Dropped file paths use the system's separators", "Dropped file paths use the system's separators"),
])
def test_note(subject, expected):
    assert release_notes.note(subject) == expected


def test_notes_cover_commits_since_previous_tag(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def git(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True)

    git("init", "-q")
    git("commit", "-q", "--allow-empty", "-m", "0.1.0: first")
    git("tag", "v0.1.0")
    git("commit", "-q", "--allow-empty", "-m", "faster torrents")
    git("commit", "-q", "--allow-empty", "-m", "0.2.0: new icon")
    git("tag", "v0.2.0")

    assert release_notes.notes("v0.1.0") == "## What's new\n\n- First\n"
    assert release_notes.notes("v0.2.0", "o/r") == (
        "## What's new\n\n- Faster torrents\n- New icon\n\n"
        "**Full changelog**: https://github.com/o/r/compare/v0.1.0...v0.2.0\n")

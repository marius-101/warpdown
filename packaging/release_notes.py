#!/usr/bin/env python3
"""Draft release notes from the commits between the previous version tag and this one.

    python3 packaging/release_notes.py v0.9.1 [--repo owner/name]

Prints Markdown: one bullet per commit subject (the "0.9.1: " version prefix dropped, merges and bare version bumps
left out) and a link comparing the two tags. The release workflow puts it on the draft release, to be edited there.
"""
import argparse
import re
import subprocess
import sys

VERSION_PREFIX = re.compile(r"^v?\d+(\.\d+)+\s*(:\s*|$)")


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def previous_tag(tag):
    """The newest v* tag reachable from before `tag`, or None for the first release."""
    try:
        return git("describe", "--tags", "--abbrev=0", "--match", "v*", f"{tag}^").strip() or None
    except subprocess.CalledProcessError:
        return None


def note(subject):
    """A commit subject as a release note, or None when nothing is left once the version prefix is gone."""
    text = VERSION_PREFIX.sub("", subject.strip()).strip()
    if not text:
        return None
    return text[0].upper() + text[1:]


def notes(tag, repo=None):
    prev = previous_tag(tag)
    span = f"{prev}..{tag}" if prev else tag
    subjects = git("log", "--no-merges", "--reverse", "--format=%s", span).splitlines()
    lines = [n for n in map(note, subjects) if n]
    out = ["## What's new", ""]
    out += [f"- {n}" for n in lines] or ["- No changes since the last release."]
    if prev and repo:
        out += ["", f"**Full changelog**: https://github.com/{repo}/compare/{prev}...{tag}"]
    return "\n".join(out) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("tag", help="the tag being released, e.g. v0.9.1")
    p.add_argument("--repo", help="owner/name on GitHub, for the compare link")
    a = p.parse_args(argv)
    sys.stdout.write(notes(a.tag, a.repo))


if __name__ == "__main__":
    main()

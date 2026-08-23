"""Export the in-app changelog entry as GitHub-ready release notes."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from shazam_for_vrc.release_notes import notes_for_version  # noqa: E402


def export_release_notes(version: str, output_directory: Path) -> Path:
    """Write GitHub Markdown from the exact notes bundled in the app."""

    notes = notes_for_version(version)
    if notes is None:
        raise ValueError(f"No in-app release notes exist for version {version}.")
    text = (
        f"# Shazam for VRC {notes.version}\n\n"
        f"## {notes.title}\n\n"
        + "\n".join(f"- {change}" for change in notes.changes)
        + "\n\n"
        "The installer includes Python, the Windows loopback-audio component, FFmpeg, and "
        "FFprobe. Existing settings and recognition history are preserved when updating.\n"
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    output_path = output_directory / f"GITHUB-RELEASE-NOTES-{version}.md"
    output_path.write_text(text, encoding="utf-8")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    arguments = parser.parse_args()
    print(export_release_notes(arguments.version, arguments.output_directory))


if __name__ == "__main__":
    main()

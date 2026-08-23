from pathlib import Path

import pytest

from shazam_for_vrc.output.history import (
    HistoryEntry,
    HistoryError,
    HistoryStore,
    TrackLogKind,
    copyable_log_text,
)


def entry(number: int) -> HistoryEntry:
    return HistoryEntry(
        recognized_at=f"2026-08-20T12:0{number}:00+02:00",
        artist=f"Artist {number}",
        title=f"Track {number}",
        world_name=f"World {number}",
    )


def test_history_is_newest_first_bounded_and_copyable(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history.json", limit=2)

    assert store.add(entry(1)) == [entry(1)]
    assert store.add(entry(2)) == [entry(2), entry(1)]
    assert store.add(entry(3)) == [entry(3), entry(2)]

    loaded = store.load()
    assert loaded == [entry(3), entry(2)]
    assert loaded[0].copy_text == "Artist 3 — Track 3"
    assert loaded[0].display_track == "Artist 3 — Track 3"
    assert loaded[0].display_time == "2026-08-20 12:03"


def test_missing_history_is_empty(tmp_path: Path) -> None:
    assert HistoryStore(tmp_path / "missing.json").load() == []


def test_invalid_history_is_reported_without_overwriting_it(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    path.write_text("not-json", encoding="utf-8")

    with pytest.raises(HistoryError, match="invalid"):
        HistoryStore(path).load()

    assert path.read_text(encoding="utf-8") == "not-json"


def test_loads_old_history_without_new_context_fields(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    path.write_text(
        """[
  {
    "recognized_at": "2026-08-20T12:00:00+02:00",
    "artist": "Artist",
    "title": "Track",
    "world_name": "Club"
  }
]\n""",
        encoding="utf-8",
    )

    loaded = HistoryStore(path).load()

    assert loaded[0].provider == "unknown"
    assert loaded[0].group_id is None
    assert loaded[0].group_name is None
    assert loaded[0].instance_type is None
    assert loaded[0].link is None
    assert loaded[0].copied_at is None


def test_group_display_hides_raw_id_and_builds_group_page() -> None:
    item = HistoryEntry(
        recognized_at="2026-08-20T12:00:00+02:00",
        artist="Artist",
        title="Track",
        world_name="Club",
        group_id="grp_71a7ff59-112c-4e78-a990-c7cc650776e5",
    )

    assert item.display_group == "Group instance"
    assert item.group_url == (
        "https://vrchat.com/home/group/grp_71a7ff59-112c-4e78-a990-c7cc650776e5"
    )


def test_group_name_and_copied_state_are_persisted(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history.json")
    item = HistoryEntry(
        recognized_at="2026-08-20T12:00:00+02:00",
        artist="Artist",
        title="Track",
        world_name="Club",
        group_id="grp_71a7ff59-112c-4e78-a990-c7cc650776e5",
        group_name="Example Group",
    ).with_copied_time()

    store.save([item])

    loaded = store.load()[0]
    assert loaded.display_group == "Example Group"
    assert loaded.copied_at is not None


def test_mix_and_error_details_are_persisted_and_errors_are_never_copied(
    tmp_path: Path,
) -> None:
    store = HistoryStore(tmp_path / "history.json")
    mix = HistoryEntry.create(
        artist="Mix",
        title="Long mix",
        world_name="Club",
        provider="youtube",
        kind=TrackLogKind.MIX,
        notice="The playback position in this mix is unknown.",
        recognition_provider="media_metadata",
    )
    error = HistoryEntry.create_error(
        stage="Recognition",
        message="No song found after 2 recording attempts.",
        world_name="Club",
        recording_attempts=2,
    )

    store.save([error, mix])
    loaded = store.load()

    assert loaded[0].kind is TrackLogKind.ERROR
    assert loaded[0].primary_text == "Listening failed — Recognition"
    assert not loaded[0].is_copyable
    assert loaded[1].kind is TrackLogKind.MIX
    assert loaded[1].primary_text == "Long mix"
    assert copyable_log_text(loaded) == "Mix — Long mix"

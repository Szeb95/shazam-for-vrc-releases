from shazam_for_vrc import __version__
from shazam_for_vrc.release_notes import RELEASE_NOTES, notes_for_version


def test_current_version_is_first_in_release_notes() -> None:
    assert RELEASE_NOTES[0].version == __version__
    assert notes_for_version(__version__) == RELEASE_NOTES[0]


def test_unknown_release_has_no_bundled_notes() -> None:
    assert notes_for_version("99.0.0") is None

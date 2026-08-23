"""Application entry point."""

from __future__ import annotations

import sys


def main() -> None:
    """Start Shazam for VRC."""

    if "--self-test" in sys.argv[1:]:
        from shazam_for_vrc.self_test import run_packaged_self_test

        run_packaged_self_test()
        return

    from shazam_for_vrc.ui import run_overlay

    run_overlay()


if __name__ == "__main__":
    main()

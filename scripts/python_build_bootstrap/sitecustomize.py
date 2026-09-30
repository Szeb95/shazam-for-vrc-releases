"""Initialize Tcl for the relocatable Windows Python used by packaging."""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path


def _initialize_windows_tcl() -> None:
    if sys.platform != "win32":
        return

    runtime_root = Path(getattr(sys, "_MEIPASS", sys.base_prefix))
    tcl_candidates = (
        runtime_root / "tcl86t.dll",
        runtime_root / "DLLs" / "tcl86t.dll",
    )
    tcl_library = next((path for path in tcl_candidates if path.is_file()), None)
    if tcl_library is None:
        return

    library = ctypes.WinDLL(str(tcl_library))
    find_executable = library.Tcl_FindExecutable
    find_executable.argtypes = [ctypes.c_char_p]
    find_executable.restype = None
    find_executable(os.fsencode(Path(sys.executable).resolve()))


_initialize_windows_tcl()

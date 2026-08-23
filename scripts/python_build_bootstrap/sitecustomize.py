"""Initialize Tcl for the relocatable Windows Python used by packaging."""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path


def _initialize_windows_tcl() -> None:
    if sys.platform != "win32":
        return
    runtime_root = Path(sys.base_prefix)
    tcl_library = runtime_root / "DLLs" / "tcl86t.dll"
    python_executable = runtime_root / "python.exe"
    if not tcl_library.is_file() or not python_executable.is_file():
        return

    library = ctypes.WinDLL(str(tcl_library))
    find_executable = library.Tcl_FindExecutable
    find_executable.argtypes = [ctypes.c_char_p]
    find_executable.restype = None
    find_executable(os.fsencode(python_executable))


_initialize_windows_tcl()

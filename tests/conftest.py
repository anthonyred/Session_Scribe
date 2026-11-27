import os
import sys

# Ensure global test timeout watchdog is active
import tests  # noqa: F401

# Ensure TCL_LIBRARY and TK_LIBRARY are consistently set on Windows
if sys.platform == "win32":
    tcl_dir = os.path.join(sys.base_prefix, "tcl", "tcl8.6")
    tk_dir = os.path.join(sys.base_prefix, "tcl", "tk8.6")
    if os.path.isdir(tcl_dir):
        os.environ["TCL_LIBRARY"] = tcl_dir
    if os.path.isdir(tk_dir):
        os.environ["TK_LIBRARY"] = tk_dir


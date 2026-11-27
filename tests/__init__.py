"""
tests package initialization.
Installs a lightweight global per-test timeout watchdog across the entire test suite
using a single background monitor thread (preventing thread churn with Tk/Tcl).
"""

from __future__ import annotations

import os
import sys
import threading
import time
import traceback
import unittest

# If RUN_SLOW_TESTS=1 is active, allow up to 300s per slow test by default
_default_timeout = "300.0" if os.environ.get("RUN_SLOW_TESTS") == "1" else "60.0"
_GLOBAL_TEST_TIMEOUT = float(os.environ.get("SESSION_SCRIBE_TEST_TIMEOUT", _default_timeout))


class _GlobalWatchdog:
    def __init__(self, timeout_sec: float):
        self.timeout_sec = timeout_sec
        self.active_test_lock = threading.Lock()
        self.active_test = None  # (test_id, start_time)
        self.stopped = False
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True, name="TestWatchdogThread")
        self.thread.start()

    def set_active_test(self, test_id: str):
        with self.active_test_lock:
            self.active_test = (test_id, time.time())

    def clear_active_test(self):
        with self.active_test_lock:
            self.active_test = None

    def _monitor_loop(self):
        while not self.stopped:
            time.sleep(0.5)
            with self.active_test_lock:
                if self.active_test is not None:
                    test_id, start_t = self.active_test
                    elapsed = time.time() - start_t
                    if elapsed > self.timeout_sec:
                        print(f"\n[GLOBAL TEST TIMEOUT EXCEEDED: {self.timeout_sec}s] Test hung: {test_id} (elapsed={elapsed:.1f}s)")
                        sys.stdout.flush()
                        for th in threading.enumerate():
                            stack = sys._current_frames().get(th.ident)
                            if stack:
                                print(f"\n--- Thread: {th.name} (ident={th.ident}) ---")
                                traceback.print_stack(stack)
                        sys.stdout.flush()
                        os._exit(1)


_watchdog = None
if _GLOBAL_TEST_TIMEOUT > 0 and not getattr(unittest.TestCase, "_session_scribe_timeout_installed", False):
    _watchdog = _GlobalWatchdog(_GLOBAL_TEST_TIMEOUT)
    _orig_run = unittest.TestCase.run

    def _timeout_wrapped_run(self, result=None):
        if _watchdog:
            _watchdog.set_active_test(self.id())
        try:
            return _orig_run(self, result)
        finally:
            if _watchdog:
                _watchdog.clear_active_test()

    unittest.TestCase.run = _timeout_wrapped_run
    unittest.TestCase._session_scribe_timeout_installed = True

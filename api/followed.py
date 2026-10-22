"""
Config files HDPay follows while it runs: the chains file.

The file is loaded at start, then looked at again by `refresh()`, at most once a second. A changed
file is reloaded; an invalid one is logged and ignored, keeping what was loaded before, and read
again on every look until it is fixed, so an editor still saving it is not a problem.
"""

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Generic, TypeVar

T = TypeVar("T")
log = logging.getLogger("hdpay.config")


class ConfigFileError(RuntimeError):
    """A followed config file is not valid."""


class FollowedFile(Generic[T]):
    """The parsed contents of a file, swapped as a whole on reload so readers never see half of it."""

    def __init__(self, path: Path, load: Callable[[Path], T], what: str):
        self.path = path
        self._load = load
        self._what = what            # for the log: "chains", "products"
        self._value = load(path)
        self._stamp = self._file_stamp()
        self._checked = time.monotonic()
        self._error = ""
        self._lock = threading.Lock()

    def _file_stamp(self) -> tuple[int, int] | None:
        try:
            stat = self.path.stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size

    def describe(self, value: T) -> str:
        """What the log says was loaded."""
        return ", ".join(value) if isinstance(value, dict) else f"{len(value)} entries"   # type: ignore[arg-type]

    def refresh(self, min_interval: float = 1.0) -> bool:
        """Reload the file if it changed since the last look. True when its contents changed."""
        if time.monotonic() - self._checked < min_interval:
            return False
        with self._lock:
            self._checked = time.monotonic()
            stamp = self._file_stamp()
            if stamp == self._stamp and not self._error:
                return False
            self._stamp = stamp
            try:
                value = self._load(self.path)
            except ConfigFileError as exc:
                if str(exc) != self._error:
                    log.error("%s. Keeping the %s loaded before.", exc, self._what)
                self._error = str(exc)
                return False
            if self._error:
                log.info("%s file is valid again", self._what)
            self._error = ""
            if value == self._value:
                return False
            self._value = value
            log.info("%s file reloaded: %s", self._what, self.describe(value))
            return True

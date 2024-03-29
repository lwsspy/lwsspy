from dataclasses import dataclass, field
import time
from typing import Callable, ClassVar, Dict, Optional, Union
from .sec2hhmmss import sec2timestamp


class TimerError(Exception):
    """A custom exception used to report errors in use of Timer class"""


class Timer:
    timers: ClassVar[Dict[str, float]] = dict()
    name: Optional[str] = None
    text: str = "     Elapsed time: {:s} seconds"
    logger: Optional[Callable[[str], None]] = print
    _start_time = None

    def __init__(self, plogger=print):
        self.logger = plogger

    def __post_init__(self) -> None:
        """Add timer to dict of timers after initialization"""
        if self.name is not None:
            self.timers.setdefault(self.name, 0)

    def start(self) -> None:
        """Start a new timer"""
        if self._start_time is not None:
            raise TimerError("Timer is running. Use .stop() to stop it")

        self._start_time = time.perf_counter()

    def stop(self) -> float:
        """Stop the timer, and report the elapsed time"""
        if self._start_time is None:
            raise TimerError("Timer is not running. Use .start() to start it")

        # Calculate elapsed time
        elapsed_time = time.perf_counter() - self._start_time
        self._start_time = None

        # Report elapsed time
        if self.logger:
            self.logger(self.text.format(sec2timestamp(elapsed_time)))
        if self.name:
            self.timers[self.name] += elapsed_time

        return elapsed_time

    def __enter__(self):
        """Start a new timer as a context manager"""
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, exc_tb):
        """Stop the context manager timer"""
        self.stop()

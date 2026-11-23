"""Cooperative cancellation shared by a workbench generation and its model calls."""
from contextlib import contextmanager
from datetime import datetime, timezone
import socket
import threading
import time


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class GenerationCancelled(Exception):
    """User cancellation, distinct from a failed candidate or transport failure."""


class GenerationControl:
    def __init__(self, on_stage=None):
        self.cancelled = threading.Event()
        self.lock = threading.RLock()
        self.responses = []
        self.on_stage = on_stage or (lambda *args: None)
        self.cleanup = None

    def stop_runtime(self):
        if self.cleanup:
            self.cleanup()
            self.cleanup = None

    def check(self):
        if self.cancelled.is_set():
            raise GenerationCancelled('用户已停止本次生成')

    @staticmethod
    def _interrupt(response):
        # Response.close can wait for urllib3's reading lock. SocketIO retains a
        # reference even after socket.close(); on Windows shutdown alone doesn't
        # wake recv. Detach the owned handle before closing it to prevent a later
        # double close. The worker releases the Response in its finally block.
        try:
            transport=response.raw._fp.fp.raw._sock
            try:
                transport.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            socket.close(transport.detach())
        except (AttributeError, OSError):
            pass

    def request(self):
        with self.lock:
            self.cancelled.set()
            for response in self.responses:
                self._interrupt(response)

    @contextmanager
    def response(self, response):
        with self.lock:
            self.responses.append(response)
        try:
            self.check()
            yield response
            self.check()
        except Exception:
            self.check()
            raise
        finally:
            with self.lock:
                self.responses.remove(response)
            close = getattr(response, 'close', None)
            if close:
                close()

    @contextmanager
    def stage(self, name):
        self.check()
        started, clock = timestamp(), time.monotonic()
        self.on_stage(name, {'started_at': started})
        try:
            yield
        finally:
            self.on_stage(name, {'started_at': started, 'finished_at': timestamp(),
                                 'elapsed_seconds': round(time.monotonic()-clock, 3)})

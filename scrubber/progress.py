"""A dependency-free waiting indicator for interactive terminals."""
import os
import shutil
import sys
import threading
import time
from contextlib import contextmanager
from itertools import cycle


@contextmanager
def progress(label):
    """Animate stderr while work runs; stop before prompts or errors are printed."""
    stream = sys.stderr
    if not stream.isatty() or os.environ.get("TERM") == "dumb":
        yield
        return

    label = "".join(char if char.isprintable() else " " for char in label)
    started = time.monotonic()
    stopped = threading.Event()

    def draw(frame):
        elapsed = int(time.monotonic() - started)
        suffix = f" ({elapsed}s)"
        columns = shutil.get_terminal_size(fallback=(80, 24)).columns
        available = max(0, columns - len(suffix) - 3)
        line = f"{frame} {label[:available]}{suffix}"[:max(0, columns - 1)]
        stream.write("\r\033[2K" + line)
        stream.flush()

    def animate():
        for frame in cycle("/-\\|"):
            if stopped.wait(0.1):
                break
            draw(frame)

    draw("|")
    worker = threading.Thread(target=animate, name="scrubber-progress", daemon=True)
    worker.start()
    try:
        yield
    finally:
        stopped.set()
        worker.join()
        stream.write("\r\033[2K")
        stream.flush()

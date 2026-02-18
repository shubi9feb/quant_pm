"""
Atomic file write helper — cross-platform, no external dependencies.

Uses tempfile + os.fsync + os.replace to guarantee that the destination
file is either fully written or left unchanged, even on crash.
"""

import json
import os
import tempfile


def atomic_write_json(path: str, obj: dict) -> None:
    """
    Write *obj* as JSON to *path* atomically.

    1. Write to a temporary file in the same directory.
    2. fsync the temp file so data reaches disk.
    3. os.replace (atomic on POSIX & modern Windows/NTFS) temp → target.
    """
    dirn = os.path.dirname(path) or "."
    os.makedirs(dirn, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dirn, prefix=os.path.basename(path))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        # Clean up temp file on any error
        if os.path.exists(tmp):
            os.remove(tmp)
        raise

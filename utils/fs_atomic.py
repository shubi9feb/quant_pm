"""
=============================================================================
ATOMIC FILE OPERATIONS
=============================================================================
Crash-safe file writes using temp file + fsync + os.replace pattern.
"""

import os
import json
import tempfile
import logging
from typing import Any, Dict

log = logging.getLogger("fs_atomic")


def atomic_write_json(path: str, data: Dict[str, Any], encoding: str = "utf-8") -> None:
    """
    Atomically write JSON data to a file using temp-file + fsync + replace.
    
    This ensures that the target file is never left in a partial/corrupt state,
    even if the process crashes mid-write or the system loses power.
    
    Args:
        path: Target file path
        data: Dictionary to serialize as JSON
        encoding: File encoding (default: utf-8)
        
    Raises:
        OSError: If write or replace fails
        
    Example:
        atomic_write_json("portfolio_state.json", {"cash": 50000, "positions": {}})
    """
    # Create temp file in same directory as target (ensures same filesystem)
    target_dir = os.path.dirname(os.path.abspath(path))
    os.makedirs(target_dir, exist_ok=True)
    
    # Write to temporary file with deterministic name for debugging
    temp_fd, temp_path = tempfile.mkstemp(
        dir=target_dir,
        prefix=".tmp_",
        suffix=f"_{os.path.basename(path)}"
    )
    
    try:
        # Serialize JSON and write to temp file
        json_bytes = json.dumps(data, indent=2, default=str).encode(encoding)
        os.write(temp_fd, json_bytes)
        
        # Force write to disk (critical for crash safety)
        os.fsync(temp_fd)
        os.close(temp_fd)
        temp_fd = None  # Mark as closed
        
        # Atomic replace: on POSIX this is guaranteed atomic
        # On Windows (Python 3.3+) this is also atomic
        os.replace(temp_path, path)
        
        log.debug(f"[ATOMIC] Wrote {len(json_bytes)} bytes to {path}")
        
    except Exception as e:
        log.error(f"[ATOMIC] Write failed for {path}: {e}")
        # Clean up temp file on error
        if temp_fd is not None:
            try:
                os.close(temp_fd)
            except Exception:
                pass
        try:
            os.unlink(temp_path)
        except Exception:
            pass
        raise
    
    # Note: temp_path is already replaced or unlinked, no cleanup needed


def atomic_read_json(path: str, default: Dict[str, Any] = None, encoding: str = "utf-8") -> Dict[str, Any]:
    """
    Read JSON from file, returning default if file doesn't exist.
    
    Args:
        path: File path to read
        default: Value to return if file not found (default: None)
        encoding: File encoding
        
    Returns:
        Parsed JSON dictionary, or default if file not found
    """
    if not os.path.exists(path):
        return default if default is not None else {}
    
    try:
        with open(path, "r", encoding=encoding) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        log.error(f"[ATOMIC] JSON decode error in {path}: {e}")
        # Return default rather than raising, allows recovery from corruption
        return default if default is not None else {}
    except Exception as e:
        log.error(f"[ATOMIC] Read error for {path}: {e}")
        raise

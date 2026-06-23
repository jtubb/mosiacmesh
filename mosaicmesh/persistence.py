"""Persistent storage of the global settings object to settings.dat.

Uses jsonpickle to serialize the Settings instance and only writes when
the encoded form's hash has changed (save_settings_incremental). The
singleton itself lives in server.settings; this module references it
lazily via `server.settings` so it's resolved at call time, not import time.
"""
import asyncio
import logging
import time
import jsonpickle
from pathlib import Path

# Lazy server import — see Task 1 plan rationale for the singleton location.
# Functions in this module reference server.settings at call time, never at
# import time, so circular import between server.py and mosaicmesh.persistence
# resolves cleanly.

# Hash of the last successfully written encoding; None means no write
# has occurred yet. save_settings_incremental compares against this to
# skip writes when the encoded payload hasn't changed since the last save.
last_settings_hash = None


def save_settings_incremental():
    """Save settings only if they have changed"""
    import server
    global last_settings_hash
    try:
        current_settings = jsonpickle.encode(server.settings, unpicklable=True)
        current_hash = hash(current_settings)

        if last_settings_hash != current_hash:
            dst = Path("settings.dat")
            # Roll the last-known-good aside before overwriting, so a bad/empty
            # save (e.g. a cross-branch load that drops state) leaves a recoverable
            # settings.dat.bak. Best-effort: never let a backup failure block a save.
            if dst.exists():
                try:
                    import shutil
                    shutil.copy2(dst, dst.with_suffix(".dat.bak"))
                except OSError as be:
                    logging.warning("settings backup failed (continuing): %s", be)
            # Atomic write: write to a temp file then os.replace, so a crash
            # mid-write can't truncate settings.dat into an unloadable state.
            tmp = dst.with_suffix(".dat.tmp")
            with tmp.open("w", encoding="utf-8") as f:
                f.write(current_settings)
            import os
            os.replace(tmp, dst)
            last_settings_hash = current_hash
            logging.debug("Settings saved (changed)")
        else:
            logging.debug("Settings save skipped (unchanged)")
    except Exception as e:
        logging.error(f"Failed to save settings: {e}")


def saveSettings():
    """Persist settings to disk (wrapper around save_settings_incremental)."""
    save_settings_incremental()


def cleanup_old_clients(max_age_seconds=24 * 3600):
    """Remove clients that have been offline longer than max_age_seconds.
    Persists only when something was actually removed. Returns the count."""
    import server
    current_time = time.time()
    stale_keys = [
        key for key, client in server.settings.clients.items()
        if not client.isOnline and (current_time - client.lastSeen) > max_age_seconds
    ]
    for key in stale_keys:
        del server.settings.clients[key]
        try:
            asyncio.get_running_loop().create_task(server._drop_pooled_vnc(key))
        except RuntimeError:
            pass  # called outside a running loop (e.g. tests); pool cleanup is best-effort
        logging.info(f"Removed stale client {key}")
    if stale_keys:
        server.saveSettings()
    return len(stale_keys)

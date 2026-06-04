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

# Module-level hash tracker (mirrors the old server.py module-level global).
last_settings_hash = None


def save_settings_incremental():
    """Save settings only if they have changed"""
    import server
    global last_settings_hash
    try:
        current_settings = jsonpickle.encode(server.settings, unpicklable=True)
        current_hash = hash(current_settings)

        if last_settings_hash != current_hash:
            with Path("settings.dat").open("w", encoding="utf-8") as f:
                f.write(current_settings)
            last_settings_hash = current_hash
            logging.debug("Settings saved (changed)")
        else:
            logging.debug("Settings save skipped (unchanged)")
    except Exception as e:
        logging.error(f"Failed to save settings: {e}")


def saveSettings():
    """Persist settings to disk (wrapper around save_settings_incremental)."""
    import server  # noqa: F401 — ensures late-binding consistency
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

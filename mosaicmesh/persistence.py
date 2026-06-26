"""Persistent storage of the global settings object to settings.dat.

Uses jsonpickle to serialize the Settings instance and only writes when
the encoded form's hash has changed (save_settings_incremental). The
singleton itself lives in server.settings; this module references it
lazily via `server.settings` so it's resolved at call time, not import time.

Data-safety (added after the 2026-06-26 clobber):
  - SETTINGS_PATH is a module global so tests redirect writes to a tmp file
    (tests/conftest.py) instead of the repo's live settings.dat. The bug was
    that several REST/render tests called the real save against an empty mock
    Settings; the relative "settings.dat" path resolved to the cwd, so running
    the suite from the repo root overwrote the operator's live data.
  - save_settings_incremental REFUSES to overwrite a populated settings.dat with
    a fresh/empty Settings (a stray instance / test write). Belt to the conftest.
  - Each change is also copied to a timestamped backup (capped), so a single bad
    save can't destroy the only backup the way the single rolling .bak did.
"""
import asyncio
import logging
import os
import time
import jsonpickle
from pathlib import Path

# Lazy server import — see Task 1 plan rationale for the singleton location.
# Functions in this module reference server.settings at call time, never at
# import time, so circular import between server.py and mosaicmesh.persistence
# resolves cleanly.

# Where settings persist. A MODULE GLOBAL (not a literal in the save body) so
# tests can monkeypatch it to a temp path and never touch the live file.
SETTINGS_PATH = Path("settings.dat")
# Directory + cap for timestamped backups (alongside the rolling .dat.bak).
TIMESTAMPED_BACKUP_DIRNAME = "settings_backups"
MAX_TIMESTAMPED_BACKUPS = 20

# Hash of the last successfully written encoding; None means no write
# has occurred yet. save_settings_incremental compares against this to
# skip writes when the encoded payload hasn't changed since the last save.
last_settings_hash = None


def _settings_is_empty(s):
    """True iff a Settings holds NO data in any of its collections — i.e. a fresh,
    blank Settings(). Used to refuse overwriting a populated file with empty state."""
    for attr in ("displays", "scripts", "clients", "playlists", "schedules", "profiles"):
        if len(getattr(s, attr, None) or {}) > 0:
            return False
    return True


def _write_timestamped_backup(dst):
    """Copy `dst` into a `settings_backups/` dir beside it with a timestamped name,
    pruning to the most recent MAX_TIMESTAMPED_BACKUPS. Unlike the single rolling
    .dat.bak (which a second bad save rotates away within ~50s), this keeps a window
    of recoverable copies. Best-effort; never raises into the caller."""
    import shutil
    parent = dst.parent if str(dst.parent) else Path(".")
    bdir = parent / TIMESTAMPED_BACKUP_DIRNAME
    bdir.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(dst, bdir / ("settings-%s.dat" % stamp))
    backups = sorted(bdir.glob("settings-*.dat"))
    for old in backups[:-MAX_TIMESTAMPED_BACKUPS]:
        try:
            old.unlink()
        except OSError:
            pass


def save_settings_incremental():
    """Save settings only if they have changed."""
    import server
    global last_settings_hash
    try:
        current_settings = jsonpickle.encode(server.settings, unpicklable=True)
        current_hash = hash(current_settings)

        if last_settings_hash == current_hash:
            logging.debug("Settings save skipped (unchanged)")
            return

        dst = SETTINGS_PATH

        # GUARD: never overwrite a populated settings.dat with a fresh/empty state.
        # An empty encode landing on a real file is almost always an accident (a test
        # or a stray second instance writing the live file via a relative path) and
        # would destroy the operator's groups/playlists/calibration. Decode the
        # existing file only on this rare empty-state path, so normal saves pay nothing.
        if _settings_is_empty(server.settings) and dst.exists():
            try:
                existing = jsonpickle.decode(dst.read_text(encoding="utf-8"))
            except Exception:
                existing = None
            if existing is not None and not _settings_is_empty(existing):
                logging.error(
                    "REFUSING to overwrite populated %s with EMPTY settings "
                    "(0 displays/clients/playlists/schedules/profiles). This is almost "
                    "certainly a test or stray instance writing the live file — save skipped.",
                    dst)
                return

        if dst.exists():
            # Rolling last-known-good (kept for compatibility).
            try:
                import shutil
                shutil.copy2(dst, dst.with_suffix(".dat.bak"))
            except OSError as be:
                logging.warning("settings backup failed (continuing): %s", be)
            # Timestamped backup history (best-effort, capped).
            try:
                _write_timestamped_backup(dst)
            except Exception as be:
                logging.warning("timestamped settings backup failed (continuing): %s", be)

        # Atomic write: write to a temp file then os.replace, so a crash
        # mid-write can't truncate settings.dat into an unloadable state.
        tmp = dst.with_suffix(".dat.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            f.write(current_settings)
        os.replace(tmp, dst)
        last_settings_hash = current_hash
        logging.debug("Settings saved (changed)")
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

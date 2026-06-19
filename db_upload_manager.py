# coding: UTF-8
"""Database upload manager — queue on failure, batch-flush on reconnect."""

import json
import os
import time
from datetime import datetime
from threading import Thread, Lock
from PySide6.QtCore import Signal, QObject
from insert_resistance2db import insert_to_mssql


class UploadSignals(QObject):
    upload_complete = Signal(bool, str)      # (success, error_msg)
    retry_complete  = Signal(int, int, int)  # (success_count, failed_count, remaining)


class DBUploadManager:
    PENDING_FILE    = "pending_uploads.json"
    UPLOAD_TIMEOUT  = 5    # seconds per upload call
    MAX_BATCH_TIME  = 120  # wall-clock limit for one batch flush (seconds)

    # Exponential backoff for reconnection attempts
    BACKOFF_INITIAL    = 10   # first retry after 10 s
    BACKOFF_MULTIPLIER = 2    # double on each consecutive failure
    BACKOFF_MAX        = 300  # cap at 5 minutes

    def __init__(self, parent_signals=None):
        self.pending_uploads = []
        self.upload_lock     = Lock()
        self.is_uploading    = False
        self.parent_signals  = parent_signals

        # Server connectivity state
        self.server_reachable = True
        self._retry_backoff   = self.BACKOFF_INITIAL
        self._next_retry_at   = 0.0   # epoch seconds; 0 = retry immediately

        self.load_pending_uploads()

    # ── Connectivity tracking ─────────────────────────────────────────────────

    def _mark_server_down(self):
        """Record a failure and schedule the next retry window."""
        if self.server_reachable:
            # First failure after a working period — reset backoff to initial
            self.server_reachable = False
            self._retry_backoff   = self.BACKOFF_INITIAL
        self._next_retry_at = time.time() + self._retry_backoff
        print(f"[DBUploadManager] Server unreachable. "
              f"Next retry in {self._retry_backoff}s (max {self.BACKOFF_MAX}s)")
        self._retry_backoff = min(
            self._retry_backoff * self.BACKOFF_MULTIPLIER, self.BACKOFF_MAX
        )

    def _mark_server_up(self):
        """Reset backoff after a successful upload."""
        self.server_reachable = True
        self._retry_backoff   = self.BACKOFF_INITIAL
        self._next_retry_at   = 0.0

    def should_retry_now(self):
        """True when the backoff window has elapsed and a retry is due."""
        return not self.is_uploading and time.time() >= self._next_retry_at

    def seconds_until_retry(self):
        return max(0.0, self._next_retry_at - time.time())

    def get_queue_status(self):
        """Return (pending_count, seconds_until_retry, server_reachable)."""
        with self.upload_lock:
            count = len(self.pending_uploads)
        return count, self.seconds_until_retry(), self.server_reachable

    # ── Persistence ───────────────────────────────────────────────────────────

    def load_pending_uploads(self):
        try:
            if os.path.exists(self.PENDING_FILE):
                with open(self.PENDING_FILE, 'r', encoding='utf-8') as f:
                    self.pending_uploads = json.load(f)
                print(f"[DBUploadManager] Loaded {len(self.pending_uploads)} pending uploads")
            else:
                self.pending_uploads = []
        except Exception as e:
            print(f"[DBUploadManager] Error loading pending uploads: {e}")
            self.pending_uploads = []

    def save_pending_uploads(self):
        try:
            with open(self.PENDING_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.pending_uploads, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[DBUploadManager] Error saving pending uploads: {e}")

    # ── Queue management ──────────────────────────────────────────────────────

    def add_pending_upload(self, model, value, status):
        with self.upload_lock:
            record = {
                "model":         model,
                "value":         value,
                "status":        status,
                "timestamp":     datetime.now().isoformat(),
                "retry_count":   0,
            }
            self.pending_uploads.append(record)
            self.save_pending_uploads()
            count = len(self.pending_uploads)
        print(f"[DBUploadManager] Queued: {model}={value} ({status}) — {count} pending")
        return count

    def get_pending_count(self):
        with self.upload_lock:
            return len(self.pending_uploads)

    def clear_all_pending(self):
        with self.upload_lock:
            self.pending_uploads.clear()
            self.save_pending_uploads()
        print("[DBUploadManager] All pending uploads cleared")

    # ── Immediate upload ──────────────────────────────────────────────────────

    def upload_async(self, model, value, status, callback=None):
        """Upload in background. If server is known down, queue directly."""
        if not self.server_reachable:
            # Skip the network attempt — just queue it.
            count = self.add_pending_upload(model, value, status)
            wait  = int(self.seconds_until_retry())
            msg   = f"Server down — queued ({count} pending, retry in {wait}s)"
            if self.parent_signals:
                self.parent_signals.upload_complete.emit(False, msg)
            return

        def upload_worker():
            try:
                insert_to_mssql(model, value, status, timeout=self.UPLOAD_TIMEOUT)
                self._mark_server_up()
                print(f"[DBUploadManager] Upload OK: {model}={value}")
                if self.parent_signals:
                    self.parent_signals.upload_complete.emit(True, "")
            except Exception as e:
                count = self.add_pending_upload(model, value, status)
                self._mark_server_down()
                print(f"[DBUploadManager] Upload failed ({count} pending): {e}")
                if self.parent_signals:
                    self.parent_signals.upload_complete.emit(False, str(e))

        Thread(target=upload_worker, daemon=True).start()

    # ── Batch retry ───────────────────────────────────────────────────────────

    def retry_pending_uploads(self, callback=None):
        """Probe server then batch-flush ALL pending records.

        Called by the main-thread timer. Gates itself with:
        - is_uploading  — prevent concurrent batch runs
        - should_retry_now() — enforce exponential backoff window
        """
        if self.is_uploading:
            return
        if not self.should_retry_now():
            return
        if not self.pending_uploads:
            return

        def retry_worker():
            self.is_uploading = True
            success_count = 0
            fail_count    = 0
            try:
                with self.upload_lock:
                    pending_list = self.pending_uploads.copy()

                total = len(pending_list)
                print(f"[DBUploadManager] Batch retry started — {total} record(s)")
                batch_start = time.time()

                for record in pending_list:
                    if time.time() - batch_start > self.MAX_BATCH_TIME:
                        print("[DBUploadManager] Batch time limit reached, stopping")
                        break
                    try:
                        insert_to_mssql(
                            record['model'], record['value'], record['status'],
                            timeout=self.UPLOAD_TIMEOUT,
                        )
                        with self.upload_lock:
                            if record in self.pending_uploads:
                                self.pending_uploads.remove(record)
                        success_count += 1
                        self._mark_server_up()   # reset backoff on each success

                    except Exception as e:
                        # First failure means server is down again — stop the batch.
                        with self.upload_lock:
                            if record in self.pending_uploads:
                                idx = self.pending_uploads.index(record)
                                self.pending_uploads[idx]['retry_count'] += 1
                        fail_count += 1
                        self._mark_server_down()
                        print(f"[DBUploadManager] Batch stopped after {success_count} OK — "
                              f"server down again: {e}")
                        break

                remaining = self.get_pending_count()
                elapsed   = time.time() - batch_start
                print(f"[DBUploadManager] Batch done in {elapsed:.1f}s — "
                      f"{success_count} OK, {fail_count} failed, {remaining} remaining")

                if self.parent_signals:
                    self.parent_signals.retry_complete.emit(success_count, fail_count, remaining)
                elif callback:
                    callback(success_count, fail_count, remaining)

            except Exception as e:
                print(f"[DBUploadManager] Unexpected error in retry_worker: {e}")
            finally:
                try:
                    self.save_pending_uploads()
                except Exception as e:
                    print(f"[DBUploadManager] Failed to save pending uploads: {e}")
                self.is_uploading = False

        Thread(target=retry_worker, daemon=True).start()

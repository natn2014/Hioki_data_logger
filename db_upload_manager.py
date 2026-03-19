# coding: UTF-8
"""Database upload manager with retry mechanism for network disconnections"""

import json
import os
import time
from datetime import datetime
from threading import Thread, Lock
from PySide2.QtCore import Signal, QObject
from insert_resistance2db import insert_to_mssql


# Qt signal emitter for thread-safe GUI updates
class UploadSignals(QObject):
    upload_complete = Signal(bool, str)      # (success, error_msg)
    retry_complete = Signal(int, int, int)   # (success_count, failed_count, remaining)


class DBUploadManager:
    """Manages database uploads with automatic retry on network failure."""
    
    PENDING_FILE = "pending_uploads.json"
    UPLOAD_TIMEOUT = 5  # seconds - prevent hanging on connection
    MAX_RETRY_TIME = 30  # seconds - skip if retry takes too long
    
    def __init__(self, parent_signals=None):
        self.pending_uploads = []
        self.upload_lock = Lock()
        self.is_uploading = False
        self.parent_signals = parent_signals  # Qt signals for thread-safe callbacks
        self.load_pending_uploads()
    
    def load_pending_uploads(self):
        """Load pending uploads from JSON file."""
        try:
            if os.path.exists(self.PENDING_FILE):
                with open(self.PENDING_FILE, 'r', encoding='utf-8') as f:
                    self.pending_uploads = json.load(f)
                    print(f"[DBUploadManager] Loaded {len(self.pending_uploads)} pending uploads from file")
            else:
                self.pending_uploads = []
        except Exception as e:
            print(f"[DBUploadManager] Error loading pending uploads: {e}")
            self.pending_uploads = []
    
    def save_pending_uploads(self):
        """Save pending uploads to JSON file for persistence."""
        try:
            with open(self.PENDING_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.pending_uploads, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[DBUploadManager] Error saving pending uploads: {e}")
    
    def add_pending_upload(self, model, value, status):
        """Add a record to pending uploads queue."""
        with self.upload_lock:
            record = {
                "model": model,
                "value": value,
                "status": status,
                "timestamp": datetime.now().isoformat(),
                "upload_status": "pending",
                "retry_count": 0
            }
            self.pending_uploads.append(record)
            self.save_pending_uploads()
            print(f"[DBUploadManager] Added to pending queue: {model} = {value} ({status})")
            return len(self.pending_uploads)
    
    def upload_async(self, model, value, status, callback=None):
        """Upload in background thread to avoid GUI hang."""
        def upload_worker():
            try:
                print(f"[DBUploadManager] Attempting upload: {model} = {value} ({status})")
                insert_to_mssql(model, value, status, timeout=self.UPLOAD_TIMEOUT)
                print(f"[DBUploadManager] Upload successful: {model} = {value}")
                
                if self.parent_signals:
                    self.parent_signals.upload_complete.emit(True, None)
                elif callback:
                    callback(True, None)
            except Exception as e:
                print(f"[DBUploadManager] Upload failed: {e}")
                with self.upload_lock:
                    self.add_pending_upload(model, value, status)
                
                if self.parent_signals:
                    self.parent_signals.upload_complete.emit(False, str(e))
                elif callback:
                    callback(False, str(e))
        
        thread = Thread(target=upload_worker, daemon=True)
        thread.start()
    
    def retry_pending_uploads(self, callback=None):
        """Retry uploading all pending records (non-blocking with timeout)."""
        if self.is_uploading or not self.pending_uploads:
            return
        
        def retry_worker():
            self.is_uploading = True
            failed_count = 0
            success_count = 0
            retry_start_time = time.time()
            
            with self.upload_lock:
                pending_list = self.pending_uploads.copy()
            
            for i, record in enumerate(pending_list):
                # Check if retry batch is taking too long (prevent hanging)
                elapsed = time.time() - retry_start_time
                if elapsed > self.MAX_RETRY_TIME:
                    print(f"[DBUploadManager] Retry timeout ({elapsed:.1f}s > {self.MAX_RETRY_TIME}s), stopping batch")
                    break
                
                try:
                    print(f"[DBUploadManager] Retrying upload [{i+1}/{len(pending_list)}]: {record['model']} = {record['value']}")
                    insert_to_mssql(record['model'], record['value'], record['status'], timeout=self.UPLOAD_TIMEOUT)
                    
                    # Remove from pending list
                    with self.upload_lock:
                        if record in self.pending_uploads:
                            self.pending_uploads.remove(record)
                    success_count += 1
                    print(f"[DBUploadManager] Retry successful: {record['model']}")
                    
                except Exception as e:
                    print(f"[DBUploadManager] Retry failed: {e}")
                    # Update retry count
                    with self.upload_lock:
                        if record in self.pending_uploads:
                            idx = self.pending_uploads.index(record)
                            self.pending_uploads[idx]['retry_count'] += 1
                    failed_count += 1
                    time.sleep(0.2)  # Brief delay between retries
            
            # Save updated pending list
            self.save_pending_uploads()
            
            total_elapsed = time.time() - retry_start_time
            print(f"[DBUploadManager] Retry complete in {total_elapsed:.2f}s: {success_count} succeeded, {failed_count} failed, {len(self.pending_uploads)} remaining")
            
            if self.parent_signals:
                self.parent_signals.retry_complete.emit(success_count, failed_count, len(self.pending_uploads))
            elif callback:
                callback(success_count, failed_count, len(self.pending_uploads))
            
            self.is_uploading = False
        
        thread = Thread(target=retry_worker, daemon=True)
        thread.start()
    
    def get_pending_count(self):
        """Get count of pending uploads."""
        with self.upload_lock:
            return len(self.pending_uploads)
    
    def get_pending_uploads(self):
        """Get list of pending uploads."""
        with self.upload_lock:
            return self.pending_uploads.copy()
    
    def clear_all_pending(self):
        """Clear all pending uploads (permanent deletion)."""
        with self.upload_lock:
            self.pending_uploads.clear()
            self.save_pending_uploads()
        print("[DBUploadManager] All pending uploads cleared")

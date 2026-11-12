# coding: UTF-8
"""Remove URL "models" (e.g. a scanned TISI QR code "https://ppdb.tisi.go.th/...")
from the offline upload queue so they stop blocking uploads.

  sudo systemctl stop hioki-app
  python3 clean_url_models.py            # show what would change (dry run)
  python3 clean_url_models.py --apply    # back up, then clean
  sudo systemctl start hioki-app

  # Fix the model name instead of parking the records (they then upload):
  python3 clean_url_models.py --replace SRG01-WS            # dry run
  python3 clean_url_models.py --replace SRG01-WS --apply

Without --replace, records with a URL model are moved (not deleted) to
failed_uploads.json and a URL current_model in gui_mode5_config.json is cleared.
With --replace MODEL, those records keep their place in pending_uploads.json
with the model renamed, records already parked in failed_uploads.json with a
URL model are moved back into the queue renamed, and a URL current_model is
set to MODEL.
"""
import json
import os
import shutil
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PENDING = os.path.join(HERE, "pending_uploads.json")
FAILED = os.path.join(HERE, "failed_uploads.json")
CONFIG = os.path.join(HERE, "gui_mode5_config.json")

MODEL_MAX_LEN = 30  # keep in sync with insert_resistance2db.MODEL_MAX_LEN


def is_bad_model(text):
    t = (text or "").strip().lower()
    if not t:
        return False
    return (t.startswith(("https://", "http://", "www.")) or ":/" in t
            or "tisi.go.th" in t or "ppdb." in t or len(t) > MODEL_MAX_LEN)


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def backup(path, stamp):
    if os.path.exists(path):
        dst = f"{path}.bak_{stamp}"
        shutil.copy2(path, dst)
        print(f"  backup: {os.path.basename(dst)}")


def get_replace_model():
    if "--replace" not in sys.argv:
        return None
    i = sys.argv.index("--replace")
    if i + 1 >= len(sys.argv) or sys.argv[i + 1].startswith("--"):
        sys.exit("--replace needs a model name, e.g. --replace SRG01-WS")
    model = sys.argv[i + 1].strip()
    if not model or is_bad_model(model):
        sys.exit(f"Invalid replacement model: {model!r}")
    return model


def main():
    apply = "--apply" in sys.argv
    new_model = get_replace_model()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    pending = load_json(PENDING, [])
    bad = [r for r in pending if is_bad_model(r.get("model"))]
    good = [r for r in pending if not is_bad_model(r.get("model"))]
    print(f"pending_uploads.json: {len(pending)} record(s), "
          f"{len(bad)} with a URL/invalid model, {len(good)} OK")
    for m in sorted({str(r.get('model')) for r in bad}):
        print(f"  bad model: {m!r}")

    failed = load_json(FAILED, [])
    failed_bad = [r for r in failed if is_bad_model(r.get("model"))]
    if new_model and failed_bad:
        print(f"failed_uploads.json: {len(failed_bad)} record(s) with a URL "
              f"model will be moved back to the queue")

    config = load_json(CONFIG, {})
    bad_current = is_bad_model(config.get("current_model"))
    if bad_current:
        print(f"{os.path.basename(CONFIG)}: current_model is invalid: "
              f"{config.get('current_model')!r}")

    if not bad and not bad_current and not (new_model and failed_bad):
        print("Nothing to clean.")
        return
    if new_model:
        print(f"Model name will be replaced with: {new_model!r}")
    if not apply:
        print()
        print("Dry run only. Stop the app, then re-run with --apply.")
        return

    if new_model:
        # Rename in place so the queue order (oldest first) is kept.
        renamed = 0
        if bad:
            backup(PENDING, stamp)
        for r in pending:
            if is_bad_model(r.get("model")):
                r["original_model"] = r.get("model")
                r["model"] = new_model
                renamed += 1
        restored = []
        if failed_bad:
            backup(FAILED, stamp)
            if not bad:
                backup(PENDING, stamp)
            for r in failed_bad:
                r = {k: v for k, v in r.items() if k not in ("rejected_at", "error")}
                r["original_model"] = r.get("model")
                r["model"] = new_model
                r["retry_count"] = 0
                restored.append(r)
            save_json(FAILED, [r for r in failed if not is_bad_model(r.get("model"))])
        # Put restored records back in time order with the rest of the queue.
        queue = sorted(pending + restored, key=lambda r: r.get("timestamp", ""))
        save_json(PENDING, queue)
        print(f"Renamed {renamed} queued record(s) and restored "
              f"{len(restored)} from failed_uploads.json -> model {new_model!r}. "
              f"Queue now has {len(queue)} record(s).")
    elif bad:
        backup(PENDING, stamp)
        failed += [dict(r, rejected_at=datetime.now().isoformat(),
                        error="URL / invalid model (clean_url_models.py)")
                   for r in bad]
        save_json(FAILED, failed)
        save_json(PENDING, good)
        print(f"Moved {len(bad)} record(s) to failed_uploads.json; "
              f"{len(good)} left in the queue.")

    if bad_current:
        backup(CONFIG, stamp)
        config["current_model"] = new_model or ""
        save_json(CONFIG, config)
        if new_model:
            print(f"Set current_model to {new_model!r}.")
        else:
            print("Cleared current_model — scan/enter the real model after restart.")


if __name__ == "__main__":
    main()

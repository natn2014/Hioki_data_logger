# coding: UTF-8
"""EN / TH strings for the kiosk UI.

tr("key", **fmt) returns the text for the current language (default Thai),
falling back to English, then to the key itself, so a missing entry never
crashes the app. Kept free of Qt imports so it can be tested on its own.

Not translated on purpose: model names, point names, PASS / FAIL / OK / NG /
N/A, WAITING, Ω, on-screen keyboard keys, and internal logs (log_event/print).
"""

LANGUAGES = ("th", "en")
DEFAULT_LANGUAGE = "th"
_lang = DEFAULT_LANGUAGE


def set_language(code):
    global _lang
    _lang = code if code in LANGUAGES else DEFAULT_LANGUAGE
    return _lang


def get_language():
    return _lang


def tr(key, **fmt):
    entry = STRINGS.get(key)
    if entry is None:
        text = key
    else:
        text = entry.get(_lang) or entry.get("en") or key
    if fmt:
        try:
            return text.format(**fmt)
        except (KeyError, IndexError, ValueError):
            return text
    return text


STRINGS = {

    # ── top bar ───────────────────────────────────────────────────────────────
    "chip_usb":  {"en": "USB · METER",   "th": "USB · เครื่องวัด"},
    "chip_wifi": {"en": "WI-FI · UPLINK", "th": "WI-FI · สัญญาณ"},
    "chip_db":   {"en": "DATABASE",      "th": "ฐานข้อมูล"},
    "cap_model": {"en": "MODEL",         "th": "รุ่นงาน"},
    "model_hint": {"en": "tap or scan ▸", "th": "แตะ หรือ สแกน ▸"},
    "model_placeholder": {"en": "Tap to set model", "th": "แตะเพื่อตั้งรุ่นงาน"},

    "usb_connected":    {"en": "● Connected",    "th": "● เชื่อมต่อแล้ว"},
    "usb_connecting":   {"en": "◌ Connecting",   "th": "◌ กำลังเชื่อมต่อ"},
    "usb_disconnected": {"en": "○ Disconnected", "th": "○ ไม่ได้เชื่อมต่อ"},

    "db_online":    {"en": "● Online",            "th": "● ออนไลน์"},
    "db_offline":   {"en": "○ Offline",           "th": "○ ออฟไลน์"},
    "db_offline_n": {"en": "○ Offline · {n}",     "th": "○ ออฟไลน์ · {n}"},
    "db_queued":    {"en": "⬆ {n} queued",        "th": "⬆ รอส่ง {n}"},

    # ── measured panel ────────────────────────────────────────────────────────
    "cap_probe":    {"en": "PROBE",     "th": "จุดวัด"},
    "cap_measured": {"en": "MEASURED",  "th": "ค่าที่วัด"},
    "single_range": {"en": "single range", "th": "ช่วงเดียว"},
    "point_index":  {"en": "point {i} / {n}", "th": "จุด {i} / {n}"},
    "cap_upper_limit": {"en": "UPPER LIMIT", "th": "สเปคสูงสุด"},
    "cap_lower_limit": {"en": "LOWER LIMIT", "th": "สเปคต่ำสุด"},
    "tag_tap_to_edit": {"en": "TAP TO EDIT", "th": "แตะเพื่อแก้"},
    "tag_locked":      {"en": "LOCKED",      "th": "ล็อก"},

    "status_waiting_meter":  {"en": "waiting for meter…", "th": "รอเครื่องวัด…"},
    "status_connecting":     {"en": "connecting to meter…", "th": "กำลังเชื่อมต่อเครื่องวัด…"},
    "status_disconnected":   {"en": "meter disconnected — check the USB cable",
                              "th": "ไม่พบเครื่องวัด — ตรวจสาย USB"},
    "status_ready":          {"en": "ready", "th": "พร้อมวัด"},
    "status_probe_part":     {"en": " · probe the part", "th": " · แตะโพรบที่ชิ้นงาน"},
    "probe_hint":            {"en": " · probe {name}", "th": " · วัดจุด {name}"},
    "status_waiting_open":   {"en": "waiting · open circuit", "th": "รอวัด · วงจรเปิด"},
    "status_holding":        {"en": " · holding last reading", "th": " · ค้างค่าล่าสุด"},
    "status_settling":       {"en": "settling…", "th": "กำลังนิ่ง… ถือโพรบค้างไว้"},
    "status_stable":         {"en": "stable · recording…", "th": "ค่านิ่ง · กำลังบันทึก…"},
    "status_recorded":       {"en": "recorded {where}{verdict}", "th": "บันทึก {where}{verdict}"},
    "status_next":           {"en": "  →  next: {name}", "th": "  →  ถัดไป: {name}"},

    "single_hint": {
        "en": "Single-range mode — no point sequence for this model.  "
              "Tap a limit to edit it, or ✎ Edit spec to add points.",
        "th": "โหมดช่วงเดียว — รุ่นนี้ไม่มีลำดับจุดวัด  "
              "แตะช่องสเปคเพื่อแก้ค่า หรือกด ✎ แก้ไขสเปค เพื่อเพิ่มจุดวัด",
    },

    # ── verdict ───────────────────────────────────────────────────────────────
    "verdict_no_reading":       {"en": "no reading yet", "th": "ยังไม่มีผลการวัด"},
    "verdict_no_reading_model": {"en": "no reading yet for this model",
                                 "th": "ยังไม่มีผลการวัดของรุ่นนี้"},

    # ── tool buttons ──────────────────────────────────────────────────────────
    "btn_first_point": {"en": "⟲  First point", "th": "⟲  จุดแรก"},
    "btn_new_model":   {"en": "＋  New model",  "th": "＋  รุ่นใหม่"},
    "btn_edit_spec":   {"en": "✎  Edit spec",   "th": "✎  แก้ไขสเปค"},
    "btn_upload_now":  {"en": "⬆  Upload now",  "th": "⬆  ส่งข้อมูล"},
    "btn_upload_n":    {"en": "⬆  Upload ({n})", "th": "⬆  ส่งข้อมูล ({n})"},
    "btn_hold":        {"en": "◼  Hold reading", "th": "◼  ค้างค่า"},
    "btn_release":     {"en": "↧  Release",      "th": "↧  ปล่อยค่า"},

    # ── counters / data log ───────────────────────────────────────────────────
    "cap_yield":    {"en": "YIELD", "th": "% ผ่าน"},
    "counts_scope": {"en": "TODAY · THIS MODEL", "th": "วันนี้ · รุ่นนี้"},
    "cap_data_log": {"en": "DATA LOG", "th": "บันทึกข้อมูล"},
    "log_count":    {"en": "{n} READINGS", "th": "{n} รายการ"},

    "csv_ok":       {"en": "(CSV: ✓)", "th": "(CSV: ✓)"},
    "csv_error":    {"en": "(CSV Error: {e})", "th": "(CSV ผิดพลาด: {e})"},
    "db_uploading": {"en": "(DB: Uploading...)", "th": "(DB: กำลังส่ง...)"},
    "db_error":     {"en": "(DB Error: {e})", "th": "(DB ผิดพลาด: {e})"},
    "db_no_model":  {"en": "(DB: No Model)", "th": "(DB: ยังไม่ตั้งรุ่น)"},
    "db_wait":      {"en": "(DB: Wait 10s)", "th": "(DB: รอ 10 วินาที)"},

    # ── DATA LOG messages ─────────────────────────────────────────────────────
    "log_pending_values": {"en": "! {n} value(s) waiting to upload",
                           "th": "! มีข้อมูล {n} รายการรอส่ง"},
    "log_pending_specs":  {"en": "! {n} model spec(s) waiting to upload",
                           "th": "! มีสเปค {n} รุ่นรอส่ง"},
    "log_limits_from_spec": {"en": "Limits come from the point spec — use ✎ Edit spec to change them",
                             "th": "สเปคมาจากลำดับจุดวัด — กด ✎ แก้ไขสเปค เพื่อเปลี่ยนค่า"},
    "log_lower_not_below": {"en": "! Lower limit is not below the upper limit — every reading will FAIL",
                            "th": "! สเปคต่ำสุดไม่น้อยกว่าสเปคสูงสุด — ทุกการวัดจะเป็น FAIL"},
    "log_model_not_changed": {"en": "Model not changed — a URL or text over {n} characters is not a valid model",
                              "th": "ไม่เปลี่ยนรุ่น — URL หรือข้อความยาวเกิน {n} ตัวอักษร ไม่ใช่ชื่อรุ่น"},
    "log_display_mode": {"en": "Display mode → {mode}", "th": "โหมดแสดงค่า → {mode}"},
    "mode_hold":        {"en": "hold last reading", "th": "ค้างค่าล่าสุด"},
    "mode_release":     {"en": "release (show WAITING)", "th": "ปล่อยค่า (แสดง WAITING)"},
    "log_point_selected": {"en": "Point → {name} ({lo}–{hi} Ω)", "th": "จุดวัด → {name} ({lo}–{hi} Ω)"},
    "log_saving_spec":  {"en": "Saving spec: {model} ({n} points)...",
                         "th": "กำลังบันทึกสเปค: {model} ({n} จุด)..."},
    "log_spec_saved":   {"en": "✓ Spec saved: {model} ({n} points)",
                         "th": "✓ บันทึกสเปคแล้ว: {model} ({n} จุด)"},
    "log_spec_rejected": {"en": "! Spec rejected: {err}", "th": "! สเปคไม่ถูกต้อง: {err}"},
    "log_spec_queued":  {"en": "! DB unreachable — spec for {model} queued ({n} pending, will upload automatically)",
                         "th": "! ติดต่อฐานข้อมูลไม่ได้ — เก็บสเปค {model} ไว้ในเครื่อง (รอส่ง {n} รุ่น จะส่งเองอัตโนมัติ)"},
    "log_spec_queue_partial": {"en": "Spec queue: {done} uploaded, {left} still pending",
                               "th": "คิวสเปค: ส่งแล้ว {done} รอส่งอีก {left}"},
    "log_spec_queue_done": {"en": "✓ Spec queue empty — {done} model(s) uploaded",
                            "th": "✓ ส่งสเปคครบแล้ว — {done} รุ่น"},
    "log_schema_old": {"en": "! DB schema out of date — {problems}. Apply migrations/001_multipoint_spec.sql "
                             "to ENGINEER_DB (uploads will fail until then).",
                       "th": "! โครงสร้างฐานข้อมูลยังไม่อัปเดต — {problems} ให้ผู้ดูแลรัน "
                             "migrations/001_multipoint_spec.sql (จะส่งข้อมูลไม่ได้จนกว่าจะแก้)"},
    "log_barcode_ignored": {"en": "Barcode ignored — that is a URL/QR link, not a model label",
                            "th": "ไม่รับบาร์โค้ด — เป็นลิงก์ URL/QR ไม่ใช่ชื่อรุ่น"},
    "log_barcode_unknown": {"en": "Barcode: unrecognised format — {raw}",
                            "th": "บาร์โค้ด: อ่านรูปแบบไม่ได้ — {raw}"},
    "log_model_by_barcode": {"en": "Model set via barcode: {model}",
                             "th": "ตั้งรุ่นจากบาร์โค้ด: {model}"},
    "log_auto_hold": {"en": "Auto Hold enabled. Polling FETC?...",
                      "th": "เปิด Auto Hold แล้ว เริ่มอ่านค่าจากเครื่องวัด..."},
    "log_error": {"en": "[{time}] Error: {msg}", "th": "[{time}] ผิดพลาด: {msg}"},
    "log_server_back": {"en": "Server back — uploading {n} queued record(s)...",
                        "th": "เซิร์ฟเวอร์กลับมาแล้ว — กำลังส่งข้อมูลที่ค้าง {n} รายการ..."},
    "log_db_unreachable": {"en": "! DB unreachable — {n} queued, retry in {wait}",
                           "th": "! ติดต่อฐานข้อมูลไม่ได้ — รอส่ง {n} รายการ ลองใหม่ใน {wait}"},
    "log_rejected": {"en": "! {n} record(s) rejected by DB (bad data) — saved to {file}",
                     "th": "! ฐานข้อมูลไม่รับ {n} รายการ (ข้อมูลผิด) — เก็บไว้ที่ {file}"},
    "log_batch_done": {"en": "Batch upload done — {n} record(s) sent",
                       "th": "ส่งข้อมูลที่ค้างครบแล้ว — {n} รายการ"},
    "log_partial": {"en": "Partial upload: {ok}✓ — {left} pending, retry in {wait}",
                    "th": "ส่งได้บางส่วน: {ok}✓ — ค้าง {left} รายการ ลองใหม่ใน {wait}"},
    "log_retry_failed": {"en": "! Retry failed — {n} queued, next in {wait}",
                         "th": "! ส่งซ้ำไม่สำเร็จ — รอส่ง {n} รายการ ลองใหม่ใน {wait}"},
    "log_queue_empty": {"en": "Upload Now — queue already empty",
                        "th": "ส่งข้อมูล — ไม่มีข้อมูลค้าง"},
    "log_upload_busy": {"en": "Upload Now — an upload is already in progress…",
                        "th": "ส่งข้อมูล — กำลังส่งอยู่แล้ว…"},
    "log_uploading_now": {"en": "⬆ Uploading {n} pending record(s) now…",
                          "th": "⬆ กำลังส่งข้อมูลที่ค้าง {n} รายการ…"},
    "log_serial_hang": {"en": "⚠ Serial hardware hang — reconnecting…",
                        "th": "⚠ การสื่อสารกับเครื่องวัดค้าง — กำลังเชื่อมต่อใหม่…"},
    "wait_soon": {"en": "soon", "th": "อีกสักครู่"},
    "wait_seconds": {"en": "{n}s", "th": "{n} วินาที"},

    # ── popups ────────────────────────────────────────────────────────────────
    "yes": {"en": "Yes", "th": "ใช่"},
    "no":  {"en": "No",  "th": "ไม่"},
    "ok":  {"en": "OK",  "th": "ตกลง"},
    "cancel": {"en": "Cancel", "th": "ยกเลิก"},
    "msg_spec_saved_title": {"en": "Spec saved", "th": "บันทึกสเปคแล้ว"},
    "msg_spec_saved_body": {"en": "Spec for '{model}' is ready.\nSwitch to this model now?",
                            "th": "สเปคของ '{model}' พร้อมแล้ว\nเปลี่ยนไปวัดรุ่นนี้เลยหรือไม่?"},
    "msg_spec_rejected_title": {"en": "Spec rejected", "th": "สเปคไม่ถูกต้อง"},
    "msg_spec_rejected_body": {"en": "The spec for '{model}' is not valid:\n{err}",
                               "th": "สเปคของ '{model}' ไม่ถูกต้อง:\n{err}"},
    "msg_spec_queued_title": {"en": "Spec queued", "th": "เก็บสเปคไว้ในเครื่อง"},
    "msg_spec_queued_body": {
        "en": "The database is unreachable, so the spec for '{model}' was saved "
              "locally to {file}.\n\nIt is active on this device now and will upload "
              "automatically when the server is back.",
        "th": "ติดต่อฐานข้อมูลไม่ได้ จึงบันทึกสเปคของ '{model}' ไว้ในเครื่องที่ {file}\n\n"
              "ใช้งานบนเครื่องนี้ได้ทันที และจะส่งเข้าฐานข้อมูลเองเมื่อเซิร์ฟเวอร์กลับมา",
    },
    "msg_no_port_title": {"en": "No Port", "th": "ไม่พบพอร์ต"},
    "msg_no_port_body":  {"en": "No HIOKI port available.", "th": "ไม่พบพอร์ตของเครื่องวัด HIOKI"},
    "msg_conn_error_title": {"en": "Connection Error", "th": "เชื่อมต่อไม่สำเร็จ"},
    "msg_startup_title": {"en": "Startup Error", "th": "เปิดโปรแกรมไม่สำเร็จ"},
    "msg_startup_body":  {"en": "Failed to initialize application:\n{e}",
                          "th": "เริ่มโปรแกรมไม่สำเร็จ:\n{e}"},

    # ── dialog titles for numpad / keyboard ───────────────────────────────────
    "title_upper_limit": {"en": "Upper Limit (Ω)", "th": "สเปคสูงสุด (Ω)"},
    "title_lower_limit": {"en": "Lower Limit (Ω)", "th": "สเปคต่ำสุด (Ω)"},
    "title_upper_short": {"en": "Upper Ω", "th": "สูงสุด Ω"},
    "title_lower_short": {"en": "Lower Ω", "th": "ต่ำสุด Ω"},
    "title_model_name":  {"en": "Model name", "th": "ชื่อรุ่น"},
    "title_point_name":  {"en": "Point name", "th": "ชื่อจุดวัด"},

    # ── spec editor ───────────────────────────────────────────────────────────
    "spec_title": {"en": "Register / Edit Model Spec", "th": "ลงทะเบียน / แก้ไขสเปครุ่นงาน"},
    "spec_model_caption": {"en": "MODEL NAME  ·  tap to type, or scan the part barcode",
                           "th": "ชื่อรุ่น  ·  แตะเพื่อพิมพ์ หรือสแกนบาร์โค้ดชิ้นงาน"},
    "spec_model_placeholder": {"en": "e.g. 750X — or scan the part barcode",
                               "th": "เช่น 750X — หรือสแกนบาร์โค้ดชิ้นงาน"},
    "spec_points_caption": {"en": "MEASUREMENT POINTS  ·  probed in this order  ·  tap a value to edit",
                            "th": "จุดวัด  ·  เรียงตามลำดับการวัด  ·  แตะค่าเพื่อแก้ไข"},
    "spec_add_point": {"en": "＋  Add point", "th": "＋  เพิ่มจุดวัด"},
    "spec_save":      {"en": "✓  Save to DB", "th": "✓  บันทึกลงฐานข้อมูล"},
    "spec_lower":     {"en": "LOWER", "th": "ต่ำสุด"},
    "spec_upper":     {"en": "UPPER", "th": "สูงสุด"},
    "err_model_required": {"en": "model name is required", "th": "ต้องใส่ชื่อรุ่น"},
    "err_add_point":      {"en": "add at least one measurement point", "th": "ต้องมีจุดวัดอย่างน้อย 1 จุด"},
    "err_point_name":     {"en": "point {i}: name is required", "th": "จุดที่ {i}: ต้องใส่ชื่อจุด"},
    "err_point_dup":      {"en": "point {i}: duplicate name '{name}'", "th": "จุดที่ {i}: ชื่อ '{name}' ซ้ำ"},
    "err_lower_upper":    {"en": "point {i} ({name}): lower must be < upper",
                           "th": "จุดที่ {i} ({name}): ค่าต่ำสุดต้องน้อยกว่าค่าสูงสุด"},
    "card_seq": {"en": "seq {n}", "th": "ลำดับ {n}"},

    # ── object temperature / temperature correction (MSW-RS485TC) ──────────────
    "chip_temp": {"en": "OBJ TEMP", "th": "อุณหภูมิชิ้นงาน"},
    "btn_tc":    {"en": "🌡  Temp corr.", "th": "🌡  ชดเชยอุณหภูมิ"},
    "btn_back":  {"en": "←  Back", "th": "←  กลับ"},
    "tc_title":  {"en": "TEMPERATURE CORRECTION", "th": "การชดเชยอุณหภูมิ"},
    "tc_tag_on":     {"en": "TC → {t0} °C", "th": "TC → {t0} °C"},
    "tc_tag_notemp": {"en": "TC: no temperature!", "th": "TC: ไม่มีอุณหภูมิ!"},
    "log_tc_not_recorded": {"en": "⚠ TC on — {note}; reading not recorded",
                            "th": "⚠ เปิด TC — {note}; ไม่บันทึกค่านี้"},
    "log_tc_set_model": {"en": "⚠ Set a model first — TC settings are saved per model",
                         "th": "⚠ เลือกรุ่นก่อน — ค่า TC บันทึกแยกตามรุ่น"},
    "log_tc_state": {"en": "TC {state} — t₀ {t0} °C, α {alpha} ppm/°C",
                     "th": "TC {state} — t₀ {t0} °C, α {alpha} ppm/°C"},

    # ── waiting time ──────────────────────────────────────────────────────────
    "btn_waiting":   {"en": "⏱  Wait {s} s", "th": "⏱  รอ {s} วิ"},
    "title_waiting": {"en": "Show WAITING after (seconds)", "th": "แสดง WAITING หลังจาก (วินาที)"},
    "log_waiting_set": {"en": "Waiting time → {s} s", "th": "ตั้งเวลารอ → {s} วินาที"},

    # ── NG reconfirm ──────────────────────────────────────────────────────────
    "ng_title":        {"en": "NG — re-measure {name}", "th": "NG — วัดซ้ำ {name}"},
    "ng_title_single": {"en": "NG — re-measure", "th": "NG — วัดซ้ำ"},
    "ng_count":        {"en": "Reconfirm NG {n}/{max}", "th": "ยืนยัน NG {n}/{max}"},
    "ng_hint": {"en": "Probe the same point again. OK closes this. NG {max}/{max} or Confirm NG sends NG to the server.",
                "th": "วัดจุดเดิมอีกครั้ง ถ้าผ่านจะปิดหน้านี้ ถ้า NG ครบ {max}/{max} หรือกดยืนยัน NG จะส่ง NG ขึ้นเซิร์ฟเวอร์"},
    "btn_confirm_ng":  {"en": "✗  Confirm NG", "th": "✗  ยืนยัน NG"},
    "status_reconfirm": {"en": "NG · re-measure {name} ({n}/{max})", "th": "NG · วัดซ้ำ {name} ({n}/{max})"},
    "log_ng_reconfirm": {"en": "! NG {where}{value} Ω — re-measure ({n}/{max})",
                         "th": "! NG {where}{value} Ω — วัดซ้ำ ({n}/{max})"},
    "log_ng_confirmed": {"en": "✗ NG confirmed {where}{value} Ω", "th": "✗ ยืนยัน NG {where}{value} Ω"},
    "log_ng_cleared":   {"en": "✓ Re-measure OK — NG cancelled", "th": "✓ วัดซ้ำผ่าน — ยกเลิก NG"},
    "log_ng_discarded": {"en": "! NG reconfirm cancelled (point or model changed)",
                         "th": "! ยกเลิกการยืนยัน NG (เปลี่ยนจุดวัดหรือรุ่น)"},
}

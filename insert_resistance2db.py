#pip install pyodbc ก่อน import

import threading
import pyodbc
from datetime import datetime

_SERVER   = '172.18.72.16'
_DATABASE = 'ENGINEER_DB'
_USERNAME = 'engineering_user'
_PASSWORD = 'Engineering@user'
_DRIVER   = '{ODBC Driver 18 for SQL Server}'


def _open_connection(timeout=5):
    """Open an MSSQL connection with a hard deadline enforced off-thread.

    On factory LANs the TCP handshake + ODBC login can exceed the caller's
    `timeout`, so the login phase gets its own headroom (at least 15 s). The
    blocking connect runs in a daemon thread so we can enforce a deadline even
    on Linux/ARM where the OS TCP timeout can exceed the pyodbc one.
    """
    connect_timeout = max(timeout, 15)
    conn_str = (f'DRIVER={_DRIVER};SERVER={_SERVER};DATABASE={_DATABASE};'
                f'UID={_USERNAME};PWD={_PASSWORD};'
                f'Connect Timeout={connect_timeout};CommandTimeout={timeout};'
                f'TrustServerCertificate=yes;'
                f'Encrypt=yes')

    result = {'conn': None, 'error': None}

    def _connect():
        try:
            result['conn'] = pyodbc.connect(conn_str, timeout=timeout)
        except Exception as e:
            result['error'] = e

    t = threading.Thread(target=_connect, daemon=True)
    t.start()
    t.join(timeout=connect_timeout + 2)  # give pyodbc's own timeout a chance first

    if t.is_alive():
        raise RuntimeError(f"Database connection timed out after {timeout}s")
    if result['error']:
        raise RuntimeError(f"Error connecting to MSSQL: {result['error']}") from result['error']

    conn = result['conn']
    conn.timeout = timeout  # pyodbc query-level timeout (backup to CommandTimeout)
    return conn


def insert_to_mssql(model, value, status, point=None, seq=None,
                    lower=None, upper=None, timeout=5):
    """Insert one resistance reading, optionally tagged with its spec point.

    point/seq/lower/upper describe which measurement point of a multi-point
    sequence produced this reading (and the range it was judged against). They
    default to None so single-range callers keep working unchanged.
    """
    if not model or not isinstance(model, str):
        raise ValueError(f"model must be a non-empty string, got: {model!r}")
    try:
        float(value)
    except (TypeError, ValueError):
        raise ValueError(f"value must be numeric, got: {value!r}")
    if status not in ("OK", "NG", "N/A"):
        raise ValueError(f"status must be 'OK', 'NG', or 'N/A', got: {status!r}")

    conn = _open_connection(timeout)
    try:
        cursor = conn.cursor()
        current_date = datetime.now().strftime('%Y-%m-%d')
        current_time = datetime.now().strftime('%H:%M:%S')
        sql_query = """
            INSERT INTO resistance
                ([Timestamp], Resistance, Status, Model, [Date], [Time],
                 Point, Seq, LowerLimit, UpperLimit)
            VALUES (getdate(), ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (value, status, model, current_date, current_time,
                  point, seq, lower, upper)

        # Wrap execute+commit in a thread so a hung query can't block the caller
        # indefinitely even if pyodbc/GIL behaviour is non-ideal on ARM.
        exec_result = {'error': None, 'done': False}

        def _execute():
            try:
                cursor.execute(sql_query, params)
                conn.commit()
                exec_result['done'] = True
            except Exception as e:
                exec_result['error'] = e

        exec_thread = threading.Thread(target=_execute, daemon=True)
        exec_thread.start()
        exec_thread.join(timeout=timeout)

        if exec_thread.is_alive():
            raise RuntimeError(f"Database query timed out after {timeout}s")
        if exec_result['error']:
            raise RuntimeError(f"Error inserting to MSSQL: {exec_result['error']}") from exec_result['error']

        print("Data inserted successfully into MSSQL.")
        return True
    finally:
        try:
            conn.close()
        except Exception:
            pass


def fetch_model_spec(model, timeout=5):
    """Return the ordered point sequence for a model from resistance_spec.

    Returns a list of dicts [{seq, name, lower, upper}, ...] ordered by Seq,
    or [] when the model has no active spec rows. Raises on connection error
    so the caller can fall back to its local cache.
    """
    if not model or not isinstance(model, str):
        raise ValueError(f"model must be a non-empty string, got: {model!r}")

    conn = _open_connection(timeout)
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT Seq, PointName, LowerLimit, UpperLimit "
            "FROM resistance_spec WHERE Model = ? AND Active = 1 "
            "ORDER BY Seq",
            (model,),
        )
        rows = cursor.fetchall()
        return [
            {"seq": int(r[0]), "name": r[1],
             "lower": float(r[2]), "upper": float(r[3])}
            for r in rows
        ]
    finally:
        try:
            conn.close()
        except Exception:
            pass


def upsert_model_spec(model, points, timeout=5):
    """Replace a model's point sequence in resistance_spec (DELETE + INSERT).

    points: list of dicts [{name, lower, upper}, ...] in probe order; Seq is
    assigned 1-based from list order. Validates every point before touching the
    DB, then runs DELETE+INSERT in one committed transaction. Idempotent — safe
    to re-run. Returns the number of point rows written.
    """
    if not model or not isinstance(model, str):
        raise ValueError(f"model must be a non-empty string, got: {model!r}")
    model = model.strip()
    if not model:
        raise ValueError("model must not be blank")
    if not points:
        raise ValueError("at least one measurement point is required")

    # Validate up front so a bad row never leaves a half-written spec.
    rows = []
    seen = set()
    for i, p in enumerate(points, start=1):
        name = str(p.get("name", "")).strip()
        if not name:
            raise ValueError(f"point {i}: name is required")
        key = name.lower()
        if key in seen:
            raise ValueError(f"point {i}: duplicate point name {name!r}")
        seen.add(key)
        try:
            lower = float(p["lower"])
            upper = float(p["upper"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"point {i} ({name}): lower/upper must be numeric")
        if lower >= upper:
            raise ValueError(f"point {i} ({name}): lower ({lower}) must be < upper ({upper})")
        rows.append((model, i, name, lower, upper))

    conn = _open_connection(timeout)
    try:
        cursor = conn.cursor()

        # Run DELETE+INSERT in a thread so a hung query can't block the caller.
        exec_result = {'error': None}

        def _execute():
            try:
                cursor.execute("DELETE FROM resistance_spec WHERE Model = ?", (model,))
                cursor.executemany(
                    "INSERT INTO resistance_spec "
                    "(Model, Seq, PointName, LowerLimit, UpperLimit) "
                    "VALUES (?, ?, ?, ?, ?)",
                    rows,
                )
                conn.commit()
            except Exception as e:
                exec_result['error'] = e

        exec_thread = threading.Thread(target=_execute, daemon=True)
        exec_thread.start()
        exec_thread.join(timeout=timeout)

        if exec_thread.is_alive():
            raise RuntimeError(f"Database query timed out after {timeout}s")
        if exec_result['error']:
            raise RuntimeError(f"Error writing resistance_spec: {exec_result['error']}") from exec_result['error']

        print(f"resistance_spec upsert OK: {model} ({len(rows)} points)")
        return len(rows)
    finally:
        try:
            conn.close()
        except Exception:
            pass


REQUIRED_RESISTANCE_COLUMNS = ("Point", "Seq", "LowerLimit", "UpperLimit")


def check_schema(timeout=5):
    """Probe the DB for the multi-point schema. Never raises — safe at startup.

    Returns a dict:
        {
          "reachable":       bool,   # could we connect at all?
          "spec_table":      bool,   # does resistance_spec exist?
          "missing_columns": [..],   # new resistance columns not present
          "error":           str | None,  # connection/query error, if any
        }
    A transient connection failure (server down) reports reachable=False with an
    error and no "missing" claims — the caller must not treat that as a bad
    schema. When reachable, spec_table True and missing_columns empty means the
    schema is fully migrated.
    """
    result = {"reachable": False, "spec_table": False,
              "missing_columns": [], "error": None}
    try:
        conn = _open_connection(timeout)
    except Exception as e:
        result["error"] = str(e)
        return result
    try:
        result["reachable"] = True
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_NAME = 'resistance_spec'"
        )
        result["spec_table"] = cursor.fetchone()[0] > 0

        cursor.execute(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_NAME = 'resistance'"
        )
        have = {r[0].lower() for r in cursor.fetchall()}
        result["missing_columns"] = [
            col for col in REQUIRED_RESISTANCE_COLUMNS if col.lower() not in have
        ]
        return result
    except Exception as e:
        result["error"] = str(e)
        return result
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    # Manual test entry; will not run when imported
    print(check_schema())
    insert_to_mssql("TEST_upload", 111.00, "OK")
    insert_to_mssql("500D", 2.0, "OK", point="A-B", seq=1, lower=1, upper=3)
    upsert_model_spec("500D", [
        {"name": "A-B", "lower": 1, "upper": 3},
        {"name": "B-C", "lower": 4, "upper": 7},
        {"name": "D-E", "lower": 4, "upper": 9},
    ])
    print(fetch_model_spec("500D"))

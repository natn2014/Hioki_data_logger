#pip install pyodbc ก่อน import

import threading
import pyodbc
from datetime import datetime

def insert_to_mssql(model, value, status, timeout=5):
    if not model or not isinstance(model, str):
        raise ValueError(f"model must be a non-empty string, got: {model!r}")
    try:
        float(value)
    except (TypeError, ValueError):
        raise ValueError(f"value must be numeric, got: {value!r}")
    if status not in ("OK", "NG", "N/A"):
        raise ValueError(f"status must be 'OK', 'NG', or 'N/A', got: {status!r}")

    server = '172.18.72.16'
    database = 'ENGINEER_DB'
    username = 'engineering_user'
    password = 'Engineering@user'
    driver = '{ODBC Driver 18 for SQL Server}'

    # "Connect Timeout" is the correct ODBC key; pyodbc's timeout kwarg is the
    # login-timeout fallback.  Both are set so either enforcement path fires first.
    conn_str = (f'DRIVER={driver};SERVER={server};DATABASE={database};'
                f'UID={username};PWD={password};'
                f'Connect Timeout={timeout};TrustServerCertificate=yes')

    result = {'conn': None, 'error': None}

    def _connect():
        try:
            result['conn'] = pyodbc.connect(conn_str, timeout=timeout)
        except Exception as e:
            result['error'] = e

    # Run the blocking connect in a daemon thread so we can enforce a hard
    # deadline on Linux/ARM where the OS TCP timeout can exceed the pyodbc one.
    t = threading.Thread(target=_connect, daemon=True)
    t.start()
    t.join(timeout=timeout + 2)     # give pyodbc's own timeout a chance first

    if t.is_alive():
        raise RuntimeError(f"Database connection timed out after {timeout}s")
    if result['error']:
        raise RuntimeError(f"Error connecting to MSSQL: {result['error']}") from result['error']

    conn = result['conn']
    try:
        cursor = conn.cursor()
        current_date = datetime.now().strftime('%Y-%m-%d')
        current_time = datetime.now().strftime('%H:%M:%S')
        sql_query = """
            INSERT INTO resistance ([Timestamp],Resistance, Status, Model, [Date], [Time])
            VALUES (getdate(), ?, ?, ?, ?, ?)
        """
        params = (value, status, model, current_date, current_time)
        cursor.execute(sql_query, params)
        conn.commit()
        print("Data inserted successfully into MSSQL.")
        return True
    except Exception as e:
        raise RuntimeError(f"Error inserting to MSSQL: {e}") from e
    finally:
        conn.close()

if __name__ == "__main__":
    # Manual test entry; will not run when imported
    insert_to_mssql("TEST_upload", 111.00, "OK")

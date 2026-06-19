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

    # Connect Timeout covers TCP handshake + ODBC login negotiation.
    # On factory LANs this can take longer than the caller's `timeout` arg,
    # so we give the login phase its own headroom (at least 15 s).
    connect_timeout = max(timeout, 15)
    conn_str = (f'DRIVER={driver};SERVER={server};DATABASE={database};'
                f'UID={username};PWD={password};'
                f'Connect Timeout={connect_timeout};CommandTimeout={timeout};'
                f'TrustServerCertificate=yes;'
                f'Encrypt=yes')

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
    t.join(timeout=connect_timeout + 2)  # give pyodbc's own timeout a chance first

    if t.is_alive():
        raise RuntimeError(f"Database connection timed out after {timeout}s")
    if result['error']:
        raise RuntimeError(f"Error connecting to MSSQL: {result['error']}") from result['error']

    conn = result['conn']
    conn.timeout = timeout  # pyodbc query-level timeout (backup to CommandTimeout)
    try:
        cursor = conn.cursor()
        current_date = datetime.now().strftime('%Y-%m-%d')
        current_time = datetime.now().strftime('%H:%M:%S')
        sql_query = """
            INSERT INTO resistance ([Timestamp],Resistance, Status, Model, [Date], [Time])
            VALUES (getdate(), ?, ?, ?, ?, ?)
        """
        params = (value, status, model, current_date, current_time)

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

if __name__ == "__main__":
    # Manual test entry; will not run when imported
    insert_to_mssql("TEST_upload", 111.00, "OK")

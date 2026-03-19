#pip install pyodbc ก่อน import

import pyodbc
from datetime import datetime

def insert_to_mssql(model, value, status):
    # 1. Connection Parameters
    server = '172.18.72.16'
    database = 'ENGINEER_DB'
    username = 'engineering_user'
    password = 'Engineering@user'
    # Ensure the driver name matches what is installed on your system
    driver = '{ODBC Driver 18 for SQL Server}' 

    conn_str = f'DRIVER={driver};SERVER={server};DATABASE={database};UID={username};PWD={password}'

    conn = None
    try:
        # 2. Establish Connection
        conn = pyodbc.connect(conn_str)
        cursor = conn.cursor()

        # 3. Prepare Data
        current_date = datetime.now().strftime('%Y-%m-%d')
        current_time = datetime.now().strftime('%H:%M:%S')

        # 4. SQL Query (MSSQL uses '?' as placeholders)
        # Exclude timestamp columns - let SQL Server manage them automatically
        sql_query = """
            INSERT INTO resistance ([Timestamp],Resistance, Status, Model, [Date], [Time]) 
            VALUES (getdate(), ?, ?, ?, ?, ?)
        """
        params = (value, status, model, current_date, current_time)

        # 5. Execute
        cursor.execute(sql_query, params)
        conn.commit()
        print("Data inserted successfully into MSSQL.")
        return True

    except Exception as e:
        raise RuntimeError(f"Error connecting to MSSQL: {e}") from e
    
    finally:
        if conn is not None:
            conn.close()

if __name__ == "__main__":
    # Manual test entry; will not run when imported
    insert_to_mssql("1SRG14R(BRK)-MM-4FIMXA-A7", 102.0004, "OK")

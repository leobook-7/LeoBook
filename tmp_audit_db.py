import sqlite3
import os
import json

DB_PATH = r"C:\Users\Admin\Desktop\ProProjection\LeoBook\Data\Store\leobook.db"

def audit():
    if not os.path.exists(DB_PATH):
        print(f"Error: DB not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get all tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [row[0] for row in cursor.fetchall()]

    report = {}

    for table in tables:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        total_rows = cursor.fetchone()[0]
        
        cursor.execute(f"PRAGMA table_info({table})")
        columns = cursor.fetchall()
        
        table_stats = {
            "total_rows": total_rows,
            "columns": {}
        }

        for col in columns:
            col_name = col[1]
            # Count NULL or empty string
            cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE {col_name} IS NULL OR {col_name} = ''")
            empty_count = cursor.fetchone()[0]
            
            fill_rate = ((total_rows - empty_count) / total_rows * 100) if total_rows > 0 else 0
            
            table_stats["columns"][col_name] = {
                "empty_count": empty_count,
                "fill_rate": f"{fill_rate:.2f}%"
            }
        
        report[table] = table_stats

    conn.close()
    
    with open("audit_report.json", "w") as f:
        json.dump(report, f, indent=2)
    print("Audit report written to audit_report.json")

if __name__ == "__main__":
    audit()

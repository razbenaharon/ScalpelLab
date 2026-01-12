import sqlite3

def check_schema():
    conn = sqlite3.connect("ScalpelDatabase.sqlite")
    cursor = conn.cursor()
    
    print("--- recording_details ---")
    cursor.execute("PRAGMA table_info(recording_details)")
    for row in cursor.fetchall():
        print(row)
        
    print("\n--- mp4_status ---")
    cursor.execute("PRAGMA table_info(mp4_status)")
    for row in cursor.fetchall():
        print(row)
    
    conn.close()

if __name__ == "__main__":
    check_schema()


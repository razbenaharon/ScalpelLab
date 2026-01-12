import sqlite3

def check_paths():
    conn = sqlite3.connect("ScalpelDatabase.sqlite")
    cursor = conn.cursor()
    cursor.execute("SELECT path FROM mp4_status LIMIT 5")
    for row in cursor.fetchall():
        print(f"DB Path: {row[0]}")
    conn.close()

if __name__ == "__main__":
    check_paths()

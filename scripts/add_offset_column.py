import sqlite3
import os

DB_PATH = "ScalpelDatabase.sqlite"

def migrate_add_offset_column():
    if not os.path.exists(DB_PATH):
        print(f"Error: Database not found at {DB_PATH}")
        return False

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Check if column exists
    cursor.execute("PRAGMA table_info(mp4_status)")
    columns = [row[1] for row in cursor.fetchall()]

    if "offset_seconds" in columns:
        print("✓ offset_seconds column already exists")
        return True

    # Add column
    try:
        cursor.execute("ALTER TABLE mp4_status ADD COLUMN offset_seconds REAL DEFAULT 0.0")
        conn.commit()
        print("✓ offset_seconds column added successfully")
        return True
    except sqlite3.Error as e:
        print(f"✗ Migration failed: {e}")
        return False
    finally:
        conn.close()

if __name__ == "__main__":
    migrate_add_offset_column()
import os
import shutil
import re

# Configuration
SOURCE_FILES = [
    r"F:\\Room_8_Data\\Recordings\\DATA_23-07-09\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_23-08-09\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_23-08-16\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_23-09-04\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_23-09-04\\Case2\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_23-09-26\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_23-09-27\\Case2\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_23-10-01\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_23-10-02\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_23-10-22\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_23-10-23\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_23-11-08\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_23-11-19\\Case2\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_23-11-26\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_23-12-10\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-01-01\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-01-01\\Case3\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-01-07\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-01-08\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-01-08\\Case2\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-01-10\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-01-10\\Case2\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-02-06\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-02-08\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-02-08\\Case2\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-02-12\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-02-14\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-02-15\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-02-15\\Case2\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-02-15\\Case3\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-02-20\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-02-25\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-03-03\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-03-06\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-03-11\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-03-11\\Case2\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-03-11\\Case3\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-03-13\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-04-21\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-05-05\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-05-06\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-05-06\\Case2\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-05-06\\Case3\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-05-12\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-05-19\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-06-03\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-06-05\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-09-09\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-09-12\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-09-12\\Case2\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-09-15\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-09-18\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-09-18\\Case2\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-10-30\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-11-05\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-11-06\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-11-07\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-11-10\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-11-11\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-11-12\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-11-17\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-11-20\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-11-21\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-11-24\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-11-27\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-11-28\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-12-01\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-12-02\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-12-10\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-12-11\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-12-15\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-12-16\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-12-18\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-12-19\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-12-24\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_24-12-25\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_25-01-01\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_25-01-02\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_25-01-05\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_25-01-06\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_25-01-06\\Case2\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_25-01-07\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_25-01-09\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_25-01-13\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_25-01-13\\Case2\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_25-01-16\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_25-01-19\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_25-01-20\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_25-02-06\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_25-03-16\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_25-04-02\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_25-04-07\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_25-05-08\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_25-07-20\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_25-07-21\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_25-07-22\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_25-07-23\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_25-07-27\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_25-08-10\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_25-08-31\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_25-09-09\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_25-09-15\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_25-10-05\\Case1\\Monitor\\Monitor.mp4",
    r"F:\\Room_8_Data\\Recordings\\DATA_25-10-12\\Case1\\Monitor\\Monitor.mp4"
]

DESTINATION_DIR = r"I:\\Monitor"

def main():
    if not os.path.exists(DESTINATION_DIR):
        print(f"Creating destination directory: {DESTINATION_DIR}")
        try:
            os.makedirs(DESTINATION_DIR)
        except Exception as e:
            print(f"Error creating directory: {e}")
            return

    print(f"Starting copy of {len(SOURCE_FILES)} files to {DESTINATION_DIR}...")
    
    total_files = len(SOURCE_FILES)
    copied_count = 0
    fail_count = 0

    for i, src_path in enumerate(SOURCE_FILES):
        try:
            if not os.path.exists(src_path):
                print(f"[MISSING] {src_path} ({i+1}/{total_files})")
                fail_count += 1
                continue
            
            # Extract date and case number by splitting path components
            # Expected structure: ...\DATA_YY-MM-DD\CaseN\...
            norm_path = os.path.normpath(src_path)
            parts = norm_path.split(os.sep)
            
            date_part = None
            case_part = None
            
            for part in parts:
                if part.startswith("DATA_") and len(part) == 13: # DATA_YY-MM-DD is 13 chars
                    date_part = part.replace("DATA_", "")
                elif part.startswith("Case") and part[4:].isdigit():
                    case_part = part[4:]
            
            if date_part and case_part:
                # Format: monitor_date_XXX_CASE_YY.mp4
                new_name = f"monitor_date_{date_part}_CASE_{case_part}.mp4"
            else:
                print(f"[WARNING] Could not parse date/case from path: {src_path}")
                print("          Using original filename.")
                new_name = os.path.basename(src_path)

            dst_path = os.path.join(DESTINATION_DIR, new_name)
            
            shutil.copy2(src_path, dst_path)
            copied_count += 1
            print(f"[OK] {new_name} ({copied_count}/{total_files} completed, {total_files - copied_count} remaining)")
            
        except Exception as e:
            print(f"[ERROR] Failed to copy {src_path}: {e} ({i+1}/{total_files})")
            fail_count += 1

    print("\n--------------------------------------------------")
    print(f"Finished.")
    print(f"Successful: {copied_count}")
    print(f"Failed:     {fail_count}")

if __name__ == "__main__":
    main()

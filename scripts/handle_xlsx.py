import pandas as pd
import subprocess
import datetime
import os
import sys

pd.set_option('display.max_rows', None)
pd.set_option('display.max_columns', None)
pd.set_option('display.width', 1000)


# --- 1. Helper Function: Get Duration via FFprobe ---
def get_duration_ffmpeg(file_path):
    """
    Uses ffprobe (part of ffmpeg) to get duration efficiently.
    """
    if not os.path.exists(file_path):
        return "File Not Found"

    # The command asks ffprobe to return ONLY the duration in seconds
    cmd = [
        'ffprobe',
        '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        file_path
    ]

    try:
        # Run the command and capture output
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        duration_seconds = float(output)

        # Convert seconds to HH:MM:SS format
        # We round to nearest second, but you can keep decimals if needed
        return str(datetime.timedelta(seconds=int(duration_seconds)))

    except subprocess.CalledProcessError as e:
        return f"FFmpeg Error"
    except FileNotFoundError:
        return "FFmpeg not in PATH"
    except ValueError:
        return "Invalid Data"


def handle_xlsx(file_name, path_col_idx=0, time_col_idx=2):
    """
    Process an xlsx file to expand paths and calculate durations.

    Args:
        file_name: Path to the xlsx file
        path_col_idx: Column index for File Path (default: 0)
        time_col_idx: Column index for End Time (default: 2)

    Returns:
        DataFrame with processed data
    """
    print(f"Using xlsx file: {file_name}")

    # --- 2. Load and Clean Data ---
    print("1. Loading and cleaning data...")
    try:
        df = pd.read_excel(file_name)
    except FileNotFoundError:
        print(f"ERROR: Could not find {file_name}")
        raise

    # Remove completely blank rows
    df = df.dropna(how='all')

    # Ensure path column is string
    df.iloc[:, path_col_idx] = df.iloc[:, path_col_idx].astype(str)

    # # --- 3. Expand Rows (Patient & Ventilator) ---
    # print("2. Expanding rows for Patient and Ventilator files...")
    #
    # # Create Patient Monitor set
    # df_patient = df.copy()
    # # Note: Adjust the slash '/' or '\' depending on if your Excel paths already have them
    # df_patient.iloc[:, path_col_idx] = df_patient.iloc[:, path_col_idx].apply(
    #     lambda x: os.path.join(x, "Patient_Monitor", "Patient_Monitor.mp4")
    # )
    #
    # # Create Ventilator Monitor set
    # df_ventilator = df.copy()
    # df_ventilator.iloc[:, path_col_idx] = df_ventilator.iloc[:, path_col_idx].apply(
    #     lambda x: os.path.join(x, "Ventilator_Monitor", "Ventilator_Monitor.mp4")
    # )
    #
    # # Combine and Sort
    # df_final = pd.concat([df_patient, df_ventilator])
    # df_final = df_final.sort_values(by=df_final.columns[path_col_idx])
    # df_final = df_final.reset_index(drop=True)
    #
    # print(f"   -> Data expanded to {len(df_final)} rows.")
    #
    # # --- 4. Replace 'end' with Actual Duration ---
    # print("3. Checking for 'end' tags and calculating durations via FFmpeg...")
    #
    # # Find rows where the time column is exactly "end"
    # mask = df_final.iloc[:, time_col_idx] == 'end'
    # rows_to_fix = df_final[mask].index
    # total_fix = len(rows_to_fix)
    #
    # for i, idx in enumerate(rows_to_fix):
    #     current_path = df_final.iloc[idx, path_col_idx]
    #
    #     # Progress indicator
    #     print(f"   [{i + 1}/{total_fix}] Processing: {current_path}")
    #
    #     new_time = get_duration_ffmpeg(current_path)
    #
    #     # Update DataFrame
    #     df_final.iloc[idx, time_col_idx] = new_time
    #
    # # --- 5. Result ---
    # print("\n✅ Process Complete!")
    # pd.set_option('display.max_rows', None)
    # pd.set_option('display.max_colwidth', None)
    # print(df_final)

    return df


# --- CLI Support ---
if __name__ == "__main__":
    # Get xlsx path from command line argument, or use default
    file_name = sys.argv[1] if len(sys.argv) > 1 else 'times.xlsx'

    df_final = handle_xlsx(file_name)

    # Optional: Save back to Excel
    # df_final.to_excel("times_updated.xlsx", index=False)
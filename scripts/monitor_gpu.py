"""
Real-Time GPU Monitoring

Monitors GPU utilization, VRAM usage, and temperature.
Run this while processing videos to see resource usage.
"""

import subprocess
import time
import sys
import os
from datetime import datetime


def get_gpu_stats():
    """Get detailed GPU statistics."""
    try:
        # Query GPU stats
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=index,name,utilization.gpu,utilization.encoder,'
             'memory.used,memory.total,temperature.gpu,power.draw,power.limit',
             '--format=csv,noheader,nounits'],
            capture_output=True,
            text=True,
            timeout=2
        )

        if result.returncode == 0:
            parts = result.stdout.strip().split(', ')
            return {
                'index': parts[0],
                'name': parts[1],
                'gpu_util': int(parts[2]),
                'encoder_util': int(parts[3]),
                'mem_used': int(parts[4]),
                'mem_total': int(parts[5]),
                'temp': int(parts[6]),
                'power_draw': float(parts[7]),
                'power_limit': float(parts[8])
            }
    except Exception as e:
        return None


def get_nvenc_sessions():
    """Get number of active NVENC encoding sessions."""
    try:
        result = subprocess.run(
            ['nvidia-smi', 'pmon', '-c', '1', '-s', 'u'],
            capture_output=True,
            text=True,
            timeout=2
        )

        if result.returncode == 0:
            # Count lines with "C" (CUDA) or "E" (Encoder)
            lines = result.stdout.strip().split('\n')
            encoder_count = sum(1 for line in lines if 'E' in line or 'C+E' in line)
            return encoder_count
    except:
        pass
    return 0


def monitor_gpu(interval=1.0, log_file=None):
    """
    Monitor GPU in real-time.

    Args:
        interval: Update interval in seconds
        log_file: Optional file to log stats to
    """
    print("="*80)
    print("GPU MONITORING")
    print("="*80)
    print("Press Ctrl+C to stop\n")

    # Open log file if specified
    log = None
    if log_file:
        log = open(log_file, 'w')
        log.write("Timestamp,GPU_Util%,Encoder_Util%,VRAM_Used_MB,VRAM_Total_MB,Temp_C,Power_W,NVENC_Sessions\n")

    max_mem = 0
    max_util = 0
    max_encoder = 0

    try:
        while True:
            stats = get_gpu_stats()
            sessions = get_nvenc_sessions()

            if stats:
                # Clear screen (optional, comment out if you want scrolling output)
                # os.system('cls' if os.name == 'nt' else 'clear')

                timestamp = datetime.now().strftime('%H:%M:%S')

                # Track maximums
                max_mem = max(max_mem, stats['mem_used'])
                max_util = max(max_util, stats['gpu_util'])
                max_encoder = max(max_encoder, stats['encoder_util'])

                # Calculate percentages
                mem_pct = (stats['mem_used'] / stats['mem_total']) * 100
                power_pct = (stats['power_draw'] / stats['power_limit']) * 100

                # Create progress bars
                gpu_bar = '█' * (stats['gpu_util'] // 5) + '░' * (20 - stats['gpu_util'] // 5)
                encoder_bar = '█' * (stats['encoder_util'] // 5) + '░' * (20 - stats['encoder_util'] // 5)
                mem_bar = '█' * int(mem_pct // 5) + '░' * (20 - int(mem_pct // 5))

                print(f"\n[{timestamp}] {stats['name']}")
                print("-"*80)
                print(f"GPU Usage:      {gpu_bar} {stats['gpu_util']:3d}% (max: {max_util}%)")
                print(f"Encoder Usage:  {encoder_bar} {stats['encoder_util']:3d}% (max: {max_encoder}%)")
                print(f"VRAM:           {mem_bar} {stats['mem_used']:5d} / {stats['mem_total']:5d} MB ({mem_pct:.1f}%)")
                print(f"                                  (max: {max_mem} MB)")
                print(f"Temperature:    {stats['temp']}°C")
                print(f"Power:          {stats['power_draw']:.1f} / {stats['power_limit']:.1f} W ({power_pct:.1f}%)")
                print(f"NVENC Sessions: {sessions}")
                print("-"*80)

                # Log to file
                if log:
                    log.write(f"{timestamp},{stats['gpu_util']},{stats['encoder_util']},"
                             f"{stats['mem_used']},{stats['mem_total']},{stats['temp']},"
                             f"{stats['power_draw']},{sessions}\n")
                    log.flush()

            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Could not get GPU stats")

            time.sleep(interval)

    except KeyboardInterrupt:
        print("\n\nMonitoring stopped")
        print(f"\nMaximum values recorded:")
        print(f"  GPU Utilization:     {max_util}%")
        print(f"  Encoder Utilization: {max_encoder}%")
        print(f"  VRAM Used:           {max_mem} MB")

    finally:
        if log:
            log.close()
            print(f"\nLog saved to: {log_file}")


def main():
    interval = 1.0  # Update every 1 second
    log_file = "gpu_monitoring.csv"

    if len(sys.argv) > 1:
        if sys.argv[1] == '--no-log':
            log_file = None
        else:
            log_file = sys.argv[1]

    monitor_gpu(interval=interval, log_file=log_file)


if __name__ == "__main__":
    main()

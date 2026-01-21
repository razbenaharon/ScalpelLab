"""
FPS Ratio Calculator for Speed-Adjusted Videos

Calculates FPS ratios for videos that have been speed-adjusted during recording
or playback. Useful for synchronizing videos recorded at different frame rates.

Usage:
    from scripts.helpers.fast_video_formula import calculate_fps_ratio, time_to_minutes

    # Convert time to minutes (accepts HH:MM:SS or numeric minutes)
    time_to_minutes("01:40:00")  # Returns 100
    time_to_minutes(100)         # Returns 100 (pass-through)

    # Fast video (>30fps compressed to 30fps): smalltime/bigtime * 30
    calculate_fps_ratio("01:40:00", "02:00:00", is_fast_video=True)

    # Slow video: bigtime/smalltime * 30
    calculate_fps_ratio(100, 120, is_fast_video=False)

Author: Raz
"""


def time_to_minutes(time_input):
    """Convert HH:MM:SS to total minutes, or return as-is if already a number."""
    if isinstance(time_input, (int, float)):
        return time_input
    parts = time_input.split(':')
    hours = int(parts[0])
    minutes = int(parts[1])
    seconds = int(parts[2])
    return hours * 60 + minutes + seconds / 60


def calculate_fps_ratio(time1, time2, is_fast_video):
    """
    Calculate FPS ratio based on two times and video speed type.

    Args:
        time1: First time in HH:MM:SS format
        time2: Second time in HH:MM:SS format
        is_fast_video: True if video is fast (>30fps compressed to 30fps),
                       False if video is slow

    Returns:
        Calculated FPS ratio multiplied by 30
    """
    minutes1 = time_to_minutes(time1)
    minutes2 = time_to_minutes(time2)

    small_time = min(minutes1, minutes2)
    big_time = max(minutes1, minutes2)

    if is_fast_video:
        # Fast video: more than 30fps inside 30fps
        return (small_time / big_time) * 30
    else:
        # Slow video
        return (big_time / small_time) * 30



print(calculate_fps_ratio(232.44555555, 244.5647073, True))


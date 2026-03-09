# SEQ-to-MP4 Sync Status Report (2026-03-10)

## Overview

| Metric | Count |
|---|---|
| Total valid SEQ recordings (non-JUNK, size > 0) | 812 |
| Already synced (MP4 exists, size >= 1MB) | 638 |
| Not synced | 174 |
| Will sync (no changes needed) | 122 |
| Will sync (with minor script tweaks) | 16 |
| **Total syncable** | **138** |
| **Will NOT sync (unrecoverable)** | **36** |
| **After full sync: 638 + 138 = 776 / 812 (95.6%)** | |

---

## Script: `scripts/3_seq_to_mp4_convert.py`

### How it decides what to convert
Query filter (line 525-541):
- Camera in GROUP_A (`Cart_Center_2, Cart_LT_4, Cart_RT_1, General_3`) or GROUP_B (`Monitor, Patient_Monitor, Ventilator_Monitor`)
- `seq_status.size_mb >= 200`
- No existing MP4 (`mp4_status.size_mb IS NULL OR < 1`)

### Skip gates during `build_session_groups()` (lines 658-815):
1. **SEQ not found on disk** (line 683) — `build_seq_path` returns None
2. **IDX not found** (line 688) — `build_idx_path` returns None
3. **SFA epoch pre-check** (line 697-705) — first/last_frame_time outside 2015-2030 (1420070400–1924905600)
4. **IDX 0 records** (line 714) — parse returns empty
5. **IDX t_start epoch** (line 725) — outside 2015-2030
6. **IDX t_end epoch** (line 730) — outside 2015-2030
7. **Duration sanity** (line 758) — `cam_timeline.duration > 200000` (~2.3 days)

---

## WILL SYNC — 122 files (run script as-is)

| # | Date | Case | Camera | Size |
|---|---|---|---|---|
| 1 | 2022-12-04 | 1 | Cart_Center_2 | 852 MB |
| 2 | 2022-12-04 | 1 | Cart_RT_1 | 2.0 GB |
| 3 | 2022-12-04 | 1 | General_3 | 2.0 GB |
| 4 | 2022-12-07 | 1 | Cart_LT_4 | 4.8 GB |
| 5 | 2022-12-07 | 1 | Cart_RT_1 | 4.9 GB |
| 6 | 2022-12-07 | 1 | General_3 | 5.3 GB |
| 7 | 2022-12-07 | 1 | Patient_Monitor | 8.9 GB |
| 8 | 2022-12-07 | 1 | Ventilator_Monitor | 8.4 GB |
| 9 | 2022-12-19 | 1 | Cart_Center_2 | 1.0 GB |
| 10 | 2022-12-19 | 1 | Cart_LT_4 | 770 MB |
| 11 | 2022-12-19 | 1 | General_3 | 513 MB |
| 12 | 2023-01-22 | 1 | Cart_Center_2 | 7.4 GB |
| 13 | 2023-01-22 | 1 | Cart_LT_4 | 7.7 GB |
| 14 | 2023-01-22 | 1 | Cart_RT_1 | 7.4 GB |
| 15 | 2023-01-22 | 1 | General_3 | 7.9 GB |
| 16 | 2023-02-05 | 1 | Cart_Center_2 | 11.3 GB |
| 17 | 2023-02-05 | 1 | Cart_RT_1 | 11.4 GB |
| 18 | 2023-02-06 | 1 | Cart_Center_2 | 4.8 GB |
| 19 | 2023-02-06 | 1 | Cart_RT_1 | 4.8 GB |
| 20 | 2023-02-06 | 1 | General_3 | 5.7 GB |
| 21 | 2023-02-06 | 1 | Patient_Monitor | 4.6 GB |
| 22 | 2023-02-06 | 1 | Ventilator_Monitor | 4.6 GB |
| 23 | 2023-02-07 | 1 | Cart_Center_2 | 2.7 GB |
| 24 | 2023-02-07 | 1 | Cart_RT_1 | 3.0 GB |
| 25 | 2023-02-07 | 1 | General_3 | 3.6 GB |
| 26 | 2023-02-07 | 1 | Patient_Monitor | 6.1 GB |
| 27 | 2023-02-07 | 1 | Ventilator_Monitor | 6.4 GB |
| 28 | 2023-02-07 | 2 | Cart_Center_2 | 855 MB |
| 29 | 2023-02-07 | 2 | Cart_RT_1 | 904 MB |
| 30 | 2023-02-07 | 2 | General_3 | 1.1 GB |
| 31 | 2023-02-07 | 2 | Patient_Monitor | 573 MB |
| 32 | 2023-02-07 | 2 | Ventilator_Monitor | 605 MB |
| 33 | 2023-02-07 | 3 | Patient_Monitor | 1.0 GB |
| 34 | 2023-02-07 | 3 | Ventilator_Monitor | 1.1 GB |
| 35 | 2023-02-09 | 1 | Cart_Center_2 | 621 MB |
| 36 | 2023-02-09 | 1 | Cart_LT_4 | 636 MB |
| 37 | 2023-02-09 | 1 | Cart_RT_1 | 879 MB |
| 38 | 2023-02-09 | 1 | General_3 | 1.1 GB |
| 39 | 2023-02-09 | 1 | Patient_Monitor | 5.2 GB |
| 40 | 2023-02-09 | 1 | Ventilator_Monitor | 5.6 GB |
| 41 | 2023-02-09 | 2 | Cart_Center_2 | 4.8 GB |
| 42 | 2023-02-09 | 2 | Cart_RT_1 | 7.3 GB |
| 43 | 2023-02-09 | 2 | General_3 | 9.0 GB |
| 44 | 2023-02-09 | 2 | Patient_Monitor | 8.5 GB |
| 45 | 2023-02-09 | 2 | Ventilator_Monitor | 9.0 GB |
| 46 | 2023-02-12 | 1 | Cart_Center_2 | 4.3 GB |
| 47 | 2023-02-12 | 1 | Cart_LT_4 | 4.2 GB |
| 48 | 2023-02-12 | 1 | Cart_RT_1 | 6.2 GB |
| 49 | 2023-02-12 | 1 | General_3 | 7.6 GB |
| 50 | 2023-02-12 | 1 | Patient_Monitor | 7.3 GB |
| 51 | 2023-02-12 | 1 | Ventilator_Monitor | 7.6 GB |
| 52 | 2023-02-19 | 1 | Cart_Center_2 | 5.5 GB |
| 53 | 2023-02-19 | 1 | Cart_RT_1 | 8.8 GB |
| 54 | 2023-02-20 | 1 | Cart_Center_2 | 661 MB |
| 55 | 2023-02-20 | 1 | Cart_LT_4 | 645 MB |
| 56 | 2023-02-20 | 1 | Cart_RT_1 | 879 MB |
| 57 | 2023-02-20 | 1 | General_3 | 1.1 GB |
| 58 | 2023-02-20 | 1 | Patient_Monitor | 1.1 GB |
| 59 | 2023-02-20 | 1 | Ventilator_Monitor | 1.1 GB |
| 60 | 2023-02-23 | 1 | Cart_Center_2 | 2.5 GB |
| 61 | 2023-02-23 | 1 | Cart_RT_1 | 2.5 GB |
| 62 | 2023-02-23 | 1 | General_3 | 2.9 GB |
| 63 | 2023-02-23 | 1 | Patient_Monitor | 2.7 GB |
| 64 | 2023-02-23 | 1 | Ventilator_Monitor | 2.6 GB |
| 65 | 2023-02-23 | 2 | Cart_Center_2 | 2.4 GB |
| 66 | 2023-02-23 | 2 | Cart_RT_1 | 2.3 GB |
| 67 | 2023-02-23 | 2 | General_3 | 2.8 GB |
| 68 | 2023-02-23 | 2 | Patient_Monitor | 2.7 GB |
| 69 | 2023-02-23 | 2 | Ventilator_Monitor | 2.5 GB |
| 70 | 2023-02-28 | 1 | Cart_Center_2 | 2.8 GB |
| 71 | 2023-02-28 | 1 | Cart_LT_4 | 3.2 GB |
| 72 | 2023-02-28 | 1 | Cart_RT_1 | 2.9 GB |
| 73 | 2023-02-28 | 1 | General_3 | 3.8 GB |
| 74 | 2023-02-28 | 1 | Patient_Monitor | 3.6 GB |
| 75 | 2023-02-28 | 1 | Ventilator_Monitor | 3.8 GB |
| 76 | 2023-07-09 | 1 | Cart_Center_2 | 3.3 GB |
| 77 | 2023-07-09 | 1 | Cart_LT_4 | 4.0 GB |
| 78 | 2023-07-09 | 1 | Cart_RT_1 | 3.5 GB |
| 79 | 2023-07-09 | 1 | General_3 | 4.6 GB |
| 80 | 2023-07-09 | 1 | Monitor | 2.0 GB |
| 81 | 2023-07-09 | 1 | Patient_Monitor | 4.6 GB |
| 82 | 2023-07-09 | 1 | Ventilator_Monitor | 4.4 GB |
| 83 | 2023-08-09 | 1 | Cart_Center_2 | 5.2 GB |
| 84 | 2023-08-09 | 1 | Cart_LT_4 | 5.9 GB |
| 85 | 2023-08-09 | 1 | Cart_RT_1 | 5.6 GB |
| 86 | 2023-08-09 | 1 | General_3 | 6.4 GB |
| 87 | 2023-08-09 | 1 | Monitor | 3.5 GB |
| 88 | 2023-08-09 | 1 | Patient_Monitor | 6.6 GB |
| 89 | 2023-08-09 | 1 | Ventilator_Monitor | 5.8 GB |
| 90 | 2023-08-16 | 1 | Cart_Center_2 | 11.0 GB |
| 91 | 2023-08-16 | 1 | Cart_LT_4 | 12.0 GB |
| 92 | 2023-08-16 | 1 | Cart_RT_1 | 12.7 GB |
| 93 | 2023-08-16 | 1 | General_3 | 14.8 GB |
| 94 | 2023-08-16 | 1 | Monitor | 6.0 GB |
| 95 | 2023-08-16 | 1 | Patient_Monitor | 15.0 GB |
| 96 | 2023-08-16 | 1 | Ventilator_Monitor | 14.0 GB |
| 97 | 2023-09-04 | 1 | Cart_Center_2 | 2.5 GB |
| 98 | 2023-09-04 | 1 | Cart_LT_4 | 2.8 GB |
| 99 | 2023-09-04 | 1 | Cart_RT_1 | 2.8 GB |
| 100 | 2023-09-04 | 1 | General_3 | 3.3 GB |
| 101 | 2023-09-04 | 1 | Monitor | 1.7 GB |
| 102 | 2023-09-04 | 1 | Patient_Monitor | 3.3 GB |
| 103 | 2023-09-04 | 1 | Ventilator_Monitor | 3.2 GB |
| 104 | 2023-09-04 | 2 | Cart_Center_2 | 3.9 GB |
| 105 | 2023-09-04 | 2 | Cart_LT_4 | 4.4 GB |
| 106 | 2023-09-04 | 2 | Cart_RT_1 | 4.4 GB |
| 107 | 2023-09-04 | 2 | General_3 | 5.1 GB |
| 108 | 2023-09-04 | 2 | Monitor | 2.7 GB |
| 109 | 2023-09-04 | 2 | Patient_Monitor | 5.1 GB |
| 110 | 2023-09-04 | 2 | Ventilator_Monitor | 4.9 GB |
| 111 | 2023-09-04 | 3 | Cart_Center_2 | 4.0 GB |
| 112 | 2023-09-04 | 3 | Cart_LT_4 | 4.3 GB |
| 113 | 2023-09-04 | 3 | Cart_RT_1 | 4.7 GB |
| 114 | 2023-09-04 | 3 | General_3 | 5.5 GB |
| 115 | 2023-09-04 | 3 | Patient_Monitor | 5.4 GB |
| 116 | 2023-09-26 | 1 | Monitor | 1.9 GB |
| 117 | 2023-09-26 | 1 | Patient_Monitor | 974 MB |
| 118 | 2023-09-26 | 1 | Ventilator_Monitor | 1.0 GB |
| 119 | 2024-02-06 | 1 | Patient_Monitor | 782 MB |
| 120 | 2024-06-03 | 1 | Patient_Monitor | 1.6 GB |
| 121 | 2024-12-30 | 2 | Patient_Monitor | 31.4 GB |
| 122 | 2024-12-30 | 2 | Ventilator_Monitor | 28.1 GB |

**Note:** #121-122 are 50-hour continuous recordings (640x480). They will sync but take a very long time to encode.

---

## WILL SYNC WITH SCRIPT TWEAKS — 16 files (rescued)

### Fix 1: Add Injection_Port as solo group (8 files)
Change needed: add `Injection_Port` to camera groups in script

| # | Date | Case | Camera | Size |
|---|---|---|---|---|
| 123 | 2023-02-12 | 1 | Injection_Port | 2.2 GB |
| 124 | 2023-02-16 | 1 | Injection_Port | 3.6 GB |
| 125 | 2023-02-19 | 1 | Injection_Port | 6.7 GB |
| 126 | 2023-02-20 | 1 | Injection_Port | 567 MB |
| 127 | 2023-02-23 | 1 | Injection_Port | 1.5 GB |
| 128 | 2023-02-23 | 2 | Injection_Port | 1.1 GB |
| 129 | 2024-01-01 | 1 | Injection_Port | 423 MB |
| 130 | 2024-01-01 | 3 | Injection_Port | 423 MB |

### Fix 2: Lower size threshold from 200 MB to ~50 MB (4 files)
Change needed: line 538 `AND s.size_mb >= 50`

| # | Date | Case | Camera | Size |
|---|---|---|---|---|
| 131 | 2024-12-30 | 1 | Cart_Center_2 | 134 MB |
| 132 | 2024-12-30 | 1 | Cart_LT_4 | 147 MB |
| 133 | 2024-12-30 | 1 | General_3 | 184 MB |
| 134 | 2024-12-30 | 1 | Patient_Monitor | 145 MB |

### Fix 3: Run `analyze_seq_fields.py` on missing entries (3 files)
These have valid IDX cache but no `seq_field_analysis` row.

| # | Date | Case | Camera | Size |
|---|---|---|---|---|
| 135 | 2023-02-07 | 1 | Cart_LT_4 | 2.1 GB |
| 136 | 2023-02-07 | 2 | Cart_LT_4 | 962 MB |
| 137 | 2023-02-09 | 2 | Cart_LT_4 | 4.9 GB |

### Fix 4: Raise duration cap from 200,000s to 250,000s (1 file)
Change needed: line 758 `if cam_timeline.duration > 250000:`

| # | Date | Case | Camera | Size | Duration |
|---|---|---|---|---|---|
| 138 | 2025-02-06 | 1 | Ventilator_Monitor | 28.3 GB | 2.77 days |

---

## WILL NOT SYNC — 36 files (unrecoverable)

### NO IDX FILE — 24 files
The `.seq.idx` binary index was never generated by NorPix or lost during backup. Without it the script cannot determine frame byte offsets or timestamps. No workaround.

| # | Date | Case | Camera | Size | Notes |
|---|---|---|---|---|---|
| 1 | 2023-10-25 | 1 | Patient_Monitor | 2.1 GB | |
| 2 | 2023-10-25 | 1 | Ventilator_Monitor | 2.0 GB | |
| 3 | 2023-11-08 | 1 | Monitor | 1.8 GB | |
| 4 | 2023-11-08 | 1 | Ventilator_Monitor | 1.5 GB | |
| 5 | 2023-12-10 | 1 | Monitor | 2.0 GB | |
| 6 | 2023-12-10 | 1 | Ventilator_Monitor | 1.2 GB | |
| 7 | 2024-01-01 | 3 | Ventilator_Monitor | 407 MB | |
| 8 | 2024-02-12 | 1 | Ventilator_Monitor | 849 MB | |
| 9 | 2024-03-03 | 1 | Cart_Center_2 | 858 MB | |
| 10 | 2024-03-03 | 1 | Cart_RT_1 | 686 MB | |
| 11 | 2024-03-03 | 1 | General_3 | 554 MB | |
| 12 | 2024-05-05 | 1 | Ventilator_Monitor | 6.6 GB | |
| 13 | 2024-10-30 | 1 | Cart_RT_1 | 36.7 GB | BIGGEST LOSS |
| 14 | 2024-10-30 | 1 | General_3 | 31.6 GB | BIGGEST LOSS |
| 15 | 2024-10-30 | 1 | Monitor | 17.2 GB | BIGGEST LOSS |
| 16 | 2024-10-30 | 1 | Patient_Monitor | 19.9 GB | BIGGEST LOSS |
| 17 | 2024-10-30 | 1 | Ventilator_Monitor | 20.7 GB | BIGGEST LOSS |
| 18 | 2024-11-24 | 1 | Monitor | 18.9 GB | |
| 19 | 2025-01-01 | 1 | Monitor | 1.5 GB | |
| 20 | 2025-01-01 | 1 | Ventilator_Monitor | 1.4 GB | |
| 21 | 2025-02-06 | 1 | Monitor | 9.2 GB | |
| 22 | 2025-07-22 | 1 | Patient_Monitor | 2.1 GB | |
| 23 | 2025-07-22 | 1 | Ventilator_Monitor | 1.5 GB | |
| 24 | 2025-08-20 | 1 | Ventilator_Monitor | 2.8 GB | |

**2024-10-30 Case1** is the biggest single loss: 5 cameras, ~126 GB total, all missing IDX.

### CORRUPT TIMESTAMPS — 6 files
IDX exists but timestamp fields decode to impossible dates. H.264 data may be intact but sync is impossible.

| # | Date | Case | Camera | Size | Problem |
|---|---|---|---|---|---|
| 25 | 2023-02-05 | 1 | General_3 | 12.2 GB | last_frame_time = 0 (null) |
| 26 | 2025-01-16 | 1 | Monitor | 3.5 GB | last_frame_time = 0 (null) |
| 27 | 2025-02-06 | 1 | Patient_Monitor | 16.3 GB | first=year 1979, last=year 2043 |
| 28 | 2025-03-16 | 1 | Monitor | 853 MB | both timestamps = year 2000 (identical) |
| 29 | 2025-04-02 | 1 | Monitor | 893 MB | first=year 2097, last=year 2092 (backwards) |
| 30 | 2025-07-20 | 1 | Monitor | 643 MB | both timestamps = year 2091 |

### INJECTION_PORT WITHOUT IDX — 3 files
Not in scope AND missing IDX. Double blocker.

| # | Date | Case | Camera | Size |
|---|---|---|---|---|
| 31 | 2023-10-25 | 1 | Injection_Port | 3.1 GB |
| 32 | 2023-11-08 | 1 | Injection_Port | 1.9 GB |
| 33 | 2023-12-10 | 1 | Injection_Port | 1.9 GB |

### TOO SMALL + NO ANALYSIS + NO IDX — 2 files
66-71 MB Cart_LT_4 files with no analysis data, no IDX cache. Likely aborted recordings.

| # | Date | Case | Camera | Size |
|---|---|---|---|---|
| 34 | 2023-02-23 | 1 | Cart_LT_4 | 66 MB |
| 35 | 2023-02-23 | 2 | Cart_LT_4 | 71 MB |

### BAD HEADER + NO IDX — 1 file
Both SEQ header corrupt and IDX missing. Completely unrecoverable.

| # | Date | Case | Camera | Size |
|---|---|---|---|---|
| 36 | 2025-06-26 | 1 | Cart_RT_1 | 3.4 GB |

---

## Final Scorecard

| Status | Count | % of 174 | Data |
|---|---|---|---|
| WILL SYNC (as-is) | 122 | 70.1% | ~570 GB |
| WILL SYNC (rescued) | 16 | 9.2% | ~52 GB |
| **Total syncable** | **138** | **79.3%** | **~622 GB** |
| No IDX | 24 | 13.8% | ~178 GB |
| Corrupt timestamps | 6 | 3.4% | ~35 GB |
| Injection_Port no IDX | 3 | 1.7% | ~7 GB |
| Too small + no data | 2 | 1.1% | ~0.1 GB |
| Bad header | 1 | 0.6% | ~3.4 GB |
| **Total NOT syncable** | **36** | **20.7%** | **~224 GB** |

**After full sync: 638 + 138 = 776 / 812 valid recordings (95.6%)**

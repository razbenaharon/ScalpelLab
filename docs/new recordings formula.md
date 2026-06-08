
---

# Frame Mapping: OLD MP4 ↔ SEQ → NEW MP4

This document defines the mapping and transition rules between the various video formats (OLD MP4, SEQ, and NEW MP4) and specifies the end-to-end formulas.

---

## 1. The Identity Rule: OLD MP4 ↔ SEQ (Bi-directional)

**Final finding:** The mapping between the OLD MP4 and the SEQ is the **identity** in both directions.

```python
# From OLD MP4 to SEQ
K_seq = i_old

# From SEQ to OLD MP4
i_old = K_seq

```

### Meaning of the Rule

* OLD MP4 frame `i_old` corresponds exactly to SEQ frame `K_seq` of the same index, and vice versa.
* The OLD MP4 file preserves the SEQ frames in order, one-to-one, with no changes to the frame sequence.
* There is **no** resampling, frame duplication, or frame dropping between these two formats.
* There is no scaling factor, no FPS conversion, and no offset.
* The frame at index `i_old` in the OLD MP4 is byte-for-byte identical to record `K_seq` (where `K_seq = i_old`) in the `.seq.idx` file.

```text
OLD MP4 frame i_old  ==  SEQ frame i_old

```

---

## 2. Part B: SEQ → NEW MP4 Mapping

The transition formula from a SEQ frame to a frame in the NEW MP4 is:

```python
new_frame = new_pre_roll_frames + round((idx_timestamp[K_seq] - idx_timestamp[0]) * 30)

```

Where the Pre-roll variable is defined as follows:

```python
new_pre_roll_frames = round((first_frame_time - group_t_global_start) * 30)

```

### Variables Key:

* **`idx_timestamp[K_seq] - idx_timestamp[0]`**: The relative time of the frame from the start of that specific SEQ file.
* **`30`**: The target frames per second (Target FPS) of the NEW MP4.
* **`group_t_global_start`**: The minimum start time (`first_frame_time`) among all cameras within the same synchronization group.

> ⚠️ **Critical Note:** The `pre_roll` variable belongs **only** to the SEQ → NEW MP4 stage. It must **not** be applied under any circumstances when mapping between OLD MP4 and SEQ.

---

## 3. End-to-End Formula (OLD MP4 → NEW MP4)

Substituting the Identity Rule (`K_seq = i_old`) into the Part B formula yields the complete and direct mapping from OLD to NEW:

```python
new_frame = new_pre_roll_frames + round((idx_timestamp[i_old] - idx_timestamp[0]) * 30)

```

---

## 4. Exceptions

The rules and formulas above **do not apply** to the following videos/cases:

| Date | Case |
| --- | --- |
| DATA_23-09-26 | Case1 |
| DATA_23-09-27 | Case2 |
| DATA_24-01-01 | Case1 |
| DATA_24-01-08 | Case2 |
| DATA_24-02-06 | Case1 |
| DATA_24-02-20 | Case1 |

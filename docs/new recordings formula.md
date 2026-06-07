# OLD MP4 → SEQ Mapping: Identity Rule

**Final finding:** the OLD MP4 → SEQ mapping is the **identity**.

```python
K_seq = i_old
```

OLD MP4 frame `i_old` corresponds to SEQ frame `K_seq` of the same index. The
OLD MP4 preserves the SEQ frames in order, one-to-one, with no resampling,
duplication, or dropping.

---

## 1. What this means

```text
OLD MP4 frame i_old  ==  SEQ frame i_old
```

There is no scaling factor, no FPS conversion, and no offset. The frame at
index `i_old` in the OLD MP4 is byte-for-byte the same source frame as IDX
record `i_old` in the `.seq.idx` file.


---

## 3. Full mapping chain

The identity rule resolves **Part A** of the chain. Part B (SEQ → NEW MP4) is
the timestamp-based formula and is unchanged.

```text
OLD MP4 frame i_old
   │   Part A:  K_seq = i_old            (identity — this document)
   ▼
SEQ frame K_seq
   │   Part B:  timestamp formula        (see below)
   ▼
NEW MP4 frame new_frame
```

**Part B — SEQ → NEW MP4:**

```python
new_frame = new_pre_roll_frames + round((idx_timestamp[K_seq] - idx_timestamp[0]) * 30)
```

where

```python
new_pre_roll_frames = round((first_frame_time - group_t_global_start) * 30)
```

- `idx_timestamp[K_seq] - idx_timestamp[0]` — relative time of the frame from
  the start of that SEQ.
- `30` — target FPS of the NEW MP4.
- `group_t_global_start` — minimum `first_frame_time` among cameras in the
  same synchronization group.

> **Note:** `pre_roll` belongs **only** to the SEQ → NEW MP4 stage. It must
> **not** be applied when mapping OLD MP4 → SEQ.

---

## 4. End-to-end formula

Substituting `K_seq = i_old` into Part B gives the complete OLD → NEW map:

```python
new_frame = new_pre_roll_frames + round((idx_timestamp[i_old] - idx_timestamp[0]) * 30)
```

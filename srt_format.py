"""Small, dependency-free SRT formatting helpers."""


def fmt_ts(seconds):
    """Format seconds as an SRT timestamp (HH:MM:SS,mmm)."""
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def render_srt_cues_with_timing_diagnostics(cues, frame_time):
    """Render OCR cues exactly as the writer does and report end clamps.

    ``cues`` contains ``(start_frame, end_frame_inclusive, text, confidence)``
    rows. The returned SRT string intentionally preserves the existing
    next-cue-start clamp and timestamp formatting. Diagnostic rows omit cue
    text and confidence so callers can safely place them in stats metadata.
    """
    cue_list = list(cues)
    rendered = []
    clamps = []
    for index, (start_frame, end_frame, text, _confidence) in enumerate(cue_list):
        start = frame_time(start_frame)
        raw_end = frame_time(end_frame + 1)
        emitted_end = raw_end
        if index + 1 < len(cue_list):
            next_start = frame_time(cue_list[index + 1][0])
            emitted_end = min(raw_end, next_start)
        rendered.append(
            f"{index + 1}\n{fmt_ts(start)} --> {fmt_ts(emitted_end)}\n{text}\n\n"
        )
        if raw_end > emitted_end:
            clamps.append({
                "cue_index": index,
                "start_frame": int(start_frame),
                "end_frame_inclusive": int(end_frame),
                "raw_end": fmt_ts(raw_end),
                "emitted_end": fmt_ts(emitted_end),
                "clamp_delta_ms": round((raw_end - emitted_end) * 1000, 3),
            })
    return "".join(rendered), clamps

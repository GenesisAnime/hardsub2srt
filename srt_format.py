"""Small, dependency-free SRT formatting helpers."""


def fmt_ts(seconds):
    """Format seconds as an SRT timestamp (HH:MM:SS,mmm)."""
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

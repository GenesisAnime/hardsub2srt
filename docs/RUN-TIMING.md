# Run timing fields

Successful hardsub OCR runs add a `timing` object to the `.stats.json` sidecar.
It is profiling metadata only; no timing value changes OCR thresholds, sampled
frames, retry decisions, or SRT text/timestamps.

```json
{
  "timing": {
    "clock": "monotonic",
    "run_elapsed_seconds": 123.456,
    "stage_durations_seconds": {
      "first_extraction": 80.0,
      "threshold_retry": null,
      "low_confidence_recovery": 12.3,
      "artifact_generation": 4.0,
      "srt_render_and_write": 0.1,
      "review_pack": null,
      "upper_band_pass": null
    },
    "stage_status": {
      "first_extraction": "completed",
      "threshold_retry": "not_triggered",
      "low_confidence_recovery": "completed",
      "artifact_generation": "completed",
      "srt_render_and_write": "completed",
      "review_pack": "not_requested",
      "upper_band_pass": "not_requested"
    },
    "stage_durations_may_overlap": true
  }
}
```

All measurements use `time.monotonic()`. New values are serialized to three
decimal places; the threshold and recovery stage values reuse their existing
stats fields, which retain their established one-decimal precision.
`run_elapsed_seconds` starts at entry to `main()` and is sampled after optional
review-pack generation, immediately before the final stats write. It therefore
includes style detection, model setup and other work not broken out as a stage;
module imports before `main()` and the final stats JSON write itself are
excluded.

Stage meanings:

- `first_extraction`: first full scan, OCR and merge. It excludes style
  detection and any later threshold retry or recovery.
- `threshold_retry`: the existing auto-threshold retry window. It includes the
  sample scan and, if the existing gate accepts it, the optional full retry.
  It reuses the existing `auto_esik_kayit.sure_s` measurement, now measured with
  a monotonic clock.
- `low_confidence_recovery`: the existing second pass for low-confidence cues.
  It reuses `kurtarma.sure_sn`, now measured with a monotonic clock.
- `artifact_generation`: post-recovery upper-band pass (when requested), text
  corrections and local artifact generation through QA images, ending before
  stats assembly. It contains `upper_band_pass` and `srt_render_and_write`.
- `srt_render_and_write`: rendering and writing the main and `_ekran.srt`
  files.
- `review_pack`: time spent making the optional local AI review pack; `null`
  when it is not requested. It is included in total run elapsed, but not in
  `artifact_generation`.
- `upper_band_pass`: the optional upper-screen scan/OCR pass and its `_ust`
  artifacts. It is included in total run elapsed and in `artifact_generation`.

An unrun optional stage has a JSON `null` duration plus a reason in
`stage_status` (for example `not_requested`, `not_triggered`, `disabled`, or
`no_candidates`). A measured stage that completes in under one millisecond may
serialize as `0.0`; its status will still say `completed`.

These durations are deliberately not a partition. In particular, recovery is
within `run_elapsed_seconds`, the upper-band pass and SRT writing are within
`artifact_generation`, and review-pack generation is within `run_elapsed_seconds`.
Do not add stage values to infer a total. A successful OCR run writes stats
before metadata/review-pack work as before, then atomically refreshes only the
stats sidecar with final timing. The final refresh uses a unique temporary file
in the stats directory and replaces the prior stats with `os.replace`; a failed
refresh leaves the previous stats file intact and removes its temporary file.
The SRT and other sidecar contents are unchanged by timing collection.

If metadata writing or review-pack import fails after the initial stats write,
the run may exit before final timing is added; the already-written stats remain
valid and unchanged. A caught review-pack build failure is recorded with its
duration and `failed` status, then final stats timing is still written.

Softsub extraction and `--no-ocr` do not produce the hardsub stats object and
are not covered by these fields. Failed runs without an initial stats file do
not have a timing record.

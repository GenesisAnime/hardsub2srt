# Local OCR review bundle

The optional review bundle is a local export for inspecting the final OCR text against subtitle-band images. It does not call GPT, DeepSeek, another AI service, or any upload endpoint. The user may choose to share the bundle manually after reviewing its contents.

## Create a bundle

- In the local UI, open **Gelişmiş ayarlar** and select **Yerel AI inceleme paketi oluştur**. The option is off by default.
- For CLI processing, add `--review-pack`:

  ```powershell
  py -3 hardsub2srt.py "D:\Videolar\bolum.mp4" -o "D:\Altyazilar\bolum.srt" --review-pack
  ```

The resulting directory is `bolum.review-pack/` beside the requested SRT. It contains:

```text
bolum.review-pack/
  README.txt
  bolum.srt
  manifest.json
  crops/
    <job-id>-cue-00001.jpg
```

The copied SRT is the final main SRT after the current correction/post-fix stage. A crop is emitted only when the final cue's exact frame interval uniquely matches one pre-merge OCR segment; merged or ambiguous cues remain in the manifest with an unavailable crop. The selected source segment's midpoint frame is read through one shared OpenCV capture session, not a new FFmpeg process per cue. The image is cropped to that cue's OCR source band; it is not a full video frame. With `--ust-ana`, the pipeline retains whether a cue came from the main or upper-band OCR pass and uses the matching band. Crops with a width or height over 1280 pixels are proportionally downscaled before JPEG encoding. Identical encoded crops are stored once and referenced by each matching cue.

## Manifest fields

`manifest.json` is UTF-8 JSON, schema version 1. It includes:

- run-local `job_id`, cue count and unique crop count;
- copied SRT filename and a local integrity hash;
- available OCR metadata: tool version, selected device, requested/second OCR mode, ONNX Runtime version when available, languages, batch, scale, frame rate, band/mask thresholds, confidence threshold, and whether post-fix was enabled;
- per cue: immutable-in-this-bundle cue ID, final SRT order, `start_ms`, `end_ms`, final `source_text_ocr`, OCR confidence, original source segment frame index/time when preserved, source region and band coordinates, crop path/hash/dimensions, and duplicate linkage;
- explicit crop status. A missing/failed frame is marked `unavailable`; it is not silently omitted or represented by an invented image.

`source_text_ocr` is the final SRT text after post-fix, not necessarily the raw recognizer string. `ocr_confidence` comes from the OCR segment and is not a calibrated probability. `mapping_status` distinguishes an exact unique original OCR interval from a merged/ambiguous interval that has no crop. Even exact interval mapping still needs visual confirmation. The frame timestamp is estimated as `frame_index / reported_fps`; source presentation timestamps are not retained, so variable-frame-rate files may not map exactly. Accept a crop/text pair as an OCR label only after a person confirms that the image shows that exact text.

The pack limits output to 1,000 unique crops, 128 MiB total encoded data, and 8 MiB per encoded crop. Before reading a new source frame, the builder checks that the crop-count limit is not reached and that at least one full 8 MiB crop allowance remains; once either check fails, later new frames are not captured or encoded and their cues are marked unavailable. A cue with the exact same source frame and band as an already stored crop reuses that crop without capture/encoding, even after a limit is reached. The final byte allowance can remain partly unused because it is reserved conservatively for one full crop. The crop provider reuses one video capture session and releases it after packing; encoded image bytes are processed one cue at a time rather than retained for the whole episode.

The manifest intentionally omits the source video path and video hash. It does contain the SRT filename and text, crop images, cue timing, and local crop/SRT hashes. Treat the bundle as sensitive and inspect it before any manual sharing.

## Boundaries and limitations

- The optional setting is off by default to avoid adding image storage to ordinary OCR runs.
- A successful package write never overwrites an existing `<ad>.review-pack`; choose a fresh output path for another package.
- Soft-subtitle extraction has no OCR segment/frame association, so a requested review pack is reported as skipped.
- Each crop is linked to the exact final OCR segment's frame interval, using its midpoint. Text may have been corrected by the current post-fix stage, so visual confirmation is required before using the pair for OCR learning.
- The feature only prepares a local review package. It has no translation-memory fields or automatic translation learning. Translation proposals and approved target text must remain a separate, user-approved workflow as described in [the learning-system plan](LEARNING-SYSTEM-PLAN.md).
- GPU remains the default when supported; `--cpu` remains explicit. Review-package generation does not alter OCR device selection or queue order.

## Human OCR verification (Phase 2)

The local Phase 2 implementation is available in the current source branch. Open `http://127.0.0.1:8765/ocr-inceleme` in the local UI. Choose a `.review-pack` folder with **Klasör seç** or paste its path, then inspect each available crop alongside the final OCR text, timing, and confidence.

- **Görüntü doğru** records the displayed OCR text as a human-accepted source label.
- **Düzeltmeyi kaydet** records the text you entered after checking it against the image.
- **Belirsiz** records that the crop could not be read confidently and excludes it from the OCR dataset.
- **Son kararı geri al** appends an undo event. It does not remove prior events.
- **Doğrulanmış OCR veri kümesini dışa aktar** creates a new `verified-ocr-dataset-...` folder in the review pack. It contains crop files and `manifest.jsonl`, not the SRT.

The review history is `review-events.jsonl` beside the pack's manifest. Each event is append-only and records the run-local cue ID plus manifest, copied-SRT, and crop SHA-256 digests. Version 1 event records require an exact schema, UUID-format event/run IDs, a local reviewer marker, a timezone-qualified ISO timestamp, and status-specific fields. Opening a pack checks SRT identity/order/times/text, exact OCR-to-cue mapping, and safe crop paths. Crop bytes are hashed and decoded lazily for each requested image; each decision strongly re-hashes the manifest/SRT and revalidates its selected crop; export repeats the source checks and validates each crop being exported. It disables decisions if the browser image fails to load. Only JPEG crops up to 1280×1280, 8 MiB each, and 128 MiB total are eligible. Packs are capped at 5,000 cues and 16 MiB each for manifest and copied SRT. The in-memory session cache holds at most eight sessions. Event logs are streamed and capped at 64 MiB, 50,000 records, and 256 KiB per record. Exceeding the record limit blocks all review-state reads and exports; no partial prefix is used. If content changed or a record is malformed, the affected label is excluded. Merged/ambiguous cues and unavailable crops cannot be accepted as visual OCR labels.

Exports are point-in-time snapshots: a later correction or undo does not rewrite an older export. Export again to capture the new active decisions. Review records are local and can contain subtitle text and copyrighted visual crops; no provider call, telemetry, VDS transfer, or automatic sharing occurs. This feature does not modify the original SRT/manifest and does not feed `/api/ogret`, `ogren.py`, `kullanici-sozlugu.txt`, translation memory, or OCR model weights. The interface has not yet had a browser/runtime smoke test; see the Phase 2 status in [the plan](LEARNING-SYSTEM-PLAN.md).

## Manual AI translation review (Phase 3)

This is a file-based round-trip, not an AI integration. In **AI çeviri incelemesi**, only cues with an active human-accepted or human-corrected OCR decision and a valid crop are offered. Enter the draft target-language translation, select up to 100 cues, set source/target language tags, and choose **Sohbet için görselli ZIP indir**. The ZIP contains `prompt.md`, metadata-only `request.json`, `response-template.json`, and a separate JPEG under `images/` for each cue. Unzip it and attach `prompt.md`, `request.json`, `response-template.json`, and all selected `images/*.jpg` files to the ChatGPT/DeepSeek conversation. The response template contains the required output schema and exact cue/time mapping; the AI should complete it and return the JSON, which you paste into the local page. The app does not make network requests. The older **Base64 JSON isteği indir** button remains for tools that accept images encoded in JSON.

The ZIP excludes absolute paths, crop/SRT/manifest hashes, source-review event IDs, and device/account identifiers. A separate private sidecar in the app's `ai-review-requests/` folder keeps hashes and source-event IDs so that a response is accepted only for the unchanged pack and verified source. Share the ZIP's prompt, metadata, and image files only; do not share the app's internal request folder or its `.local.json` sidecar. The AI must return the request ID, exact cue IDs and timings, and the documented structured source-first response. Paste that JSON into the page to import.

Imported results are suggestions only. `translation-review-events.jsonl` keeps proposals and explicit user accept/edit/reject decisions append-only; this decision journal is the source of truth. The UI blocks acceptance unless the AI says the source image is confirmed and translation review is complete. `translation-memory-v1.jsonl` is an atomic, rebuildable snapshot of accepted or edited decisions, with a human source-event link. If snapshot writing fails, the decision remains saved, the UI says the memory view needs repair, and reopening the page or pressing **Onaylı belleği onar** retries it. This memory is not applied automatically and is never used by OCR correction or `Öğret`.

Malformed, stale, duplicated, mismatched, or schema-invalid responses are rejected/excluded. Re-import of the same request is idempotent. The maximum batch is 100 cues; each request or ZIP is capped at 12 MiB and responses at 4 MiB. Each crop is size-checked before it is added; ZIP images remain separate files. No AI provider call, upload, or video was used while implementing this feature. The user confirmed that AI review will remain a manual conversation/file round-trip; this application will not call GPT/DeepSeek APIs.

## Scoped translation memory and lexicon (Phase 4)

In the same page, enter a project/series key and use the source/target language fields to load an exact local scope. For example, `one-piece` + `ja` → `tr` is separate from another series or language pair. **Bu kapsamı yükle** shows active entries. There is no fuzzy or cross-project fallback.

Only existing Phase 3 translations that the user explicitly accepted/edited and whose human-verified source crop is still valid can be selected for **Seçili kabul edilmiş çevirileri bu belleğe ekle**. The addition is a separate user action. A phrase/term may also be added manually to the scoped lexicon. These stores are distinct from visual OCR labels and from the old OCR correction dictionary consumed by “Öğret”.

For an eligible visually verified source cue, **Bellekten öneri ara** searches the exact project/language scope. A unique exact-source translation or a case-insensitive literal lexicon candidate is shown. Literal matching supports Japanese/Chinese text. Because there is no trusted CJK tokenizer, a CJK match touching adjacent CJK-script characters is marked ambiguous instead of guessed as a complete word. Overlapping occurrences (including self-overlapping repeats such as `猫猫` within `猫猫猫`) are also ambiguous. The user must press the apply button before any candidate enters the AI translation draft. Distinct translations for the same normalized source, conflicting lexicon targets, or overlapping term matches are not applied. Suggestions never rewrite the SRT or a saved decision.

**Geri al** appends a rollback record. Event history is retained; active projections are rebuilt without the reverted item. Repeating the same rollback is safe. Files stay on the local computer under the OS user's app-data directory and are not uploaded. Each scope has separate versioned translation-memory and lexicon projections plus a bounded append-only journal. `/api/ceviri-bellegi/` accepts only `localhost:<configured-port>` and `127.0.0.1:<configured-port>` hosts. Mutations require the matching same-origin HTTP `Origin` header or a matching `Referer` fallback; the page and API must use the same loopback alias. This allowlist is intended to reject DNS-rebinding hostnames and cross-site writes. Runtime/browser acceptance and synthetic rollback/collision checks remain outstanding; do not treat the phase as runtime-verified until those checks are completed.

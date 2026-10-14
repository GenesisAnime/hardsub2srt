# Manual AI OCR visual review

**[Türkçe sürüm](AI-OCR-GORSEL-INCELEME.md) [TR]** · **English [EN]**

This workflow lets a user manually ask GPT, DeepSeek, or another vision-capable AI to review selected crops alongside the initial OCR SRT text, before a human has approved it. The app does not connect to an AI provider or send content over the network. The user adds the downloaded ZIP to a chat of their choice and pastes the AI response into the local UI.

**Sync warning:** the ZIP, private local sidecar, and proposal log are written inside the selected `.review-pack`. The app making no network calls does not prevent a sync client such as OneDrive from uploading these files. For strict local-only handling, **copy** the entire `.review-pack` (including the manifest, SRT, `review-events.jsonl`, and `crops/`) to a non-synced local directory, then open that copy in the UI. Do not move the originals or omit part of the pack.

## Use

1. Open a `.review-pack` in the local OCR review page. The **“AI görsel incelemesine dahil et”** (“Include in AI visual review”) checkbox appears on subtitle cards with valid crops that do not await human review.
2. Select up to 100 cues and click **“Seçili görsellerle AI inceleme ZIP'i indir”** (“Download AI review ZIP with selected images”). The ZIP contains `prompt.md`, `request.json`, `response-template.json`, and only the selected `images/<cue-id>.jpg` crops.
3. Add these files to an AI chat yourself. Sending crops and OCR text to an external provider is an explicit user action; review the provider's retention policy separately.
4. Get a completed response-template JSON from the AI and paste it into the local page. Import writes only to the separate `ai-ocr-review/proposals.jsonl` proposal log.
5. Inspect the crop and proposal. To accept/correct OCR text, use the normal **“Görüntü doğru”** (“Image is correct”) or **“Düzeltmeyi kaydet”** (“Save correction”) button on the card. This human decision is written separately to `review-events.jsonl`. An AI response alone cannot enter any OCR dataset export. The AI translation workflow also remains closed until a human verifies the source text.

## Data contract

The request contains each cue's ID, fixed start/end time in milliseconds, `source_text_ocr` corresponding to the SRT, OCR confidence, JPEG crop filename, and image. Only when production stats match the current main SRT by hash, a clamped cue may also include diagnostic `raw_end_ms`, `clamp_delta_ms`, and `raw_end_time_basis`. This raw value is approximate frame/FPS time; source PTS is not preserved, and it is not a timing proposal. The ZIP excludes absolute source paths, device/user-account identity, the full video, manifest/SRT hashes, and local sidecars. The prompt asks only to read subtitle text, not to mix scene text/credits/logo text into subtitles, to treat any raw-end diagnostic as no change proposal, and to mark uncertainty explicitly.

The response template and response schema use only the written `start_ms`/`end_ms` values. Raw-end diagnostics are not part of the response schema; the AI response cannot change cue timing.

The local OCR review cue card shows a read-only note only when the pack metadata's generation SRT hash matches the current SRT hash and clamp metadata is validated for that cue. The note shows the written end, approximate raw end, and amount shortened by the writer. This is not a start/end input or part of the AI response schema; it must not be used as a timing proposal. No note appears when the hash is missing/stale or the run had no clamp.

The response schema is `schemas/ai-ocr-visual-review-v1.schema.json`. Each row contains the same `cue_id`, `start_ms`, and `end_ms`, plus: `status` (`confirmed`, `correction_proposed`, `uncertain`), `observed_text`, `proposed_text`, and `reason`. Cue/timing changes, duplicate IDs, missing rows, unknown fields, invalid status, stale pack/SRT/crop, and invalid JSON are rejected. Re-importing the same response creates no new record. A different response revision may be stored as a separate proposal.

Each pack has a local `ai-ocr-review/` directory containing a request ID, pack/SRT/crop hash mapping, and a `.local.json` sidecar with mode 0600 where supported; the sidecar is not included in the ZIP. Proposals are stored in append-only `proposals.jsonl`. Records contain `schema_version: 1`, `event_type: proposal`, a random event ID, request/response hashes, cue/timing, status, proposal, and timestamp. A corrupt/out-of-schema log blocks further writes; no labels are generated from a partial log. The AI proposal log does not modify the human OCR decision log, dataset manifest, or translation memory.

## Timing and accuracy limits

A crop is associated with an OCR segment in the review pack. If the review-pack creator did not preserve source video PTS, frame/FPS-derived timing is approximate; AI review neither makes the timing reference exact nor changes timing. Even an AI `confirmed` status is not human approval. This workflow is for visual OCR comparison; AI translation/semantic evaluation is a separate manual workflow.

## Limits and rollback

- Up to 100 cues are allowed; the compressed ZIP limit is 12 MiB and the response limit is 4 MiB. Crops are rechecked using the existing review-pack size/decode/hash controls.
- Records stay only under the local review pack. Proposal history is not automatically sent/synced to the UI or an external service.
- There is no separate button to accept AI proposals. Incorrect/unwanted proposal rows remain in history; make the normal human OCR decision to supersede their effect. To remove local proposal history, first back up the `.review-pack`, then manually delete only the `ai-ocr-review/` directory. This does not touch the human decision log.

## Synthetic validation

Tests use fake JPEGs and fake subtitle text; they make no real video/OCR or provider request. They validate application integrity, not AI OCR accuracy or timing alignment.

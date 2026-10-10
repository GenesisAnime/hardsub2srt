# OCR and AI translation learning system: implementation brief

**Status:** Phases 0–1 are documented and the local Phase 1 review-pack exporter exists. Phase 2 has a local human OCR verification implementation on branch `codex/phase2-ocr-human-review`; it is not yet merged or independently runtime-validated. No LLM review service, upload endpoint, or model-training pipeline is implemented.

**Purpose:** guide a future implementation that improves (1) transcription of subtitles visible in video frames and (2) translation review of generated SRT text. These are separate tasks with separate evidence, approval, storage, and metrics.

## Role and objective for a future implementation agent

Act as an implementation agent for this repository. Inspect the current branch, code, documentation, and relevant run artifacts before proposing changes. Build the smallest useful phase of a local-first learning system. Keep OCR source-text learning separate from translation review and translation memory. Treat user-approved corrections as the authority; model suggestions are never automatically accepted or used for training.

Before broad code changes, report the current behavior, proposed files, data flow, privacy implications, and a phase-sized implementation plan. Implement only the approved phase. Preserve the existing product contract: GPU remains the default when supported; `--cpu` is an explicit opt-in. Do not run OCR over the user's library. Do not upload media, subtitles, crops, or telemetry unless the user has explicitly reviewed and approved that specific sharing action.

## Current behavior verified in this repository

- `hardsub2srt.py` reads video locally and writes timed SRT, stats/run metadata, optional QA images, and other requested outputs. Its product OCR path uses CUDA-capable PyTorch when available and falls back to CPU; `--cpu` explicitly selects CPU. Do not change these defaults.
- `ui_server.py` is a local Flask UI and starts the OCR process on the user's machine. It does not send video to a remote server.
- The optional `--qa` output is a selected set of diagnostic images for inspection. It is not currently a persistent, one-crop-per-cue labeled training dataset.
- The editor's per-block **Öğret** action (`ui_server.py`: `ogret`, `/api/ogret`) stores a whitespace-normalized `old<TAB>new` phrase pair in `kullanici-sozlugu.txt`. It sends no image, cue timing, confidence, or model information. If the block is unchanged, the UI refuses to add a rule.
- `hardsub2srt.py` loads that file in `_kullanici_sozluk_yukle` and applies its rules in `_kullanici_uygula` as literal, case-sensitive substring replacements. This is deterministic post-processing, not OCR model training. A broad rule can alter every matching occurrence.
- The editor's **Kaydet** path (`ui_server.py`: `ogrenme_yurut`) separately invokes `ogren.py` on changed SRT blocks. `ogren.py` classifies candidate word changes and records a per-SRT learning report. It still has text only; without an image/crop it cannot teach the visual recognizer.
- `vtt-qa.py` and `regresyon/gt_gate.py` compare OCR transcription with trusted reference subtitles. They do not measure translation quality. The README explicitly says translation and translation quality review are outside the current tool.
- `TOPLULUK-OGRENME-TASARIMI.md` is an earlier community-learning concept. Where it conflicts with this plan or current README/code (for example, device defaults or claims about uploaded data), use the current README and this phase plan as the implementation basis. Recheck code before treating any statement here as current behavior.
- The Phase 2 local review page is `/ocr-inceleme` in the loopback UI. Human decisions append to `review-events.jsonl` inside the review pack. The verified OCR export contains only currently active accepted/corrected crop-text pairs; uncertain, undone, stale, malformed, unavailable, or mismatched items are excluded. It has no connection to `/api/ogret`, `ogren.py`, `kullanici-sozlugu.txt`, translation memory, or any network service.

## Core separation: two learning loops

### Loop A — visual OCR source-text learning

A useful supervised OCR example is a subtitle image crop paired with the text a person has visually confirmed in that crop. Correct text without the corresponding image is not a visual OCR training example. A phrase correction without a crop can support a text rule, but cannot update recognizer weights.

For each distinct subtitle cue, capture one compressed crop from a representative frame used by or aligned with OCR. Repeated near-identical frames of the same cue should be deduplicated. If timing boundaries need investigation, optionally capture one frame near cue start and one near cue end; label these as boundary evidence, not extra independent text samples. Never capture or retain a full video as a learning artifact.

### Loop B — AI translation review and translation memory

A translation review record needs a verified source-language subtitle, a proposed/current target-language translation, and the user-approved target translation. Approved pairs can support a translation memory, terminology glossary, and style guidance. They do not teach the visual OCR recognizer.

The reviewer must inspect the crop and OCR source text first. It may review translation only after the source text is confirmed. If OCR remains uncertain, it must return a source-text issue and defer translation review; it must not guess the source from context and then store a translation pair as ground truth.

The AI produces proposals with reasons and evidence. The user accepts, edits, or rejects each proposal. Only an explicitly accepted result becomes an approved translation-memory item. Do not train or update memory from an unverified model answer, a rejected proposal, or an auto-generated translation that the user has not approved.

## Data and identity model

Use versioned records with immutable IDs. Keep local paths relative to a run/export root and never put absolute source paths, account names, or stable device IDs into a shareable package. A content hash may be used locally for deduplication; do not transmit a video hash or crop hash by default because it can link activity across submissions.

A proposed local cue manifest has this shape:

```json
{
  "schema_version": 1,
  "job_id": "random-per-run-id",
  "cues": [
    {
      "cue_id": "immutable-id-within-run",
      "start_ms": 12500,
      "end_ms": 14800,
      "source_text_ocr": "recognized source text",
      "ocr_confidence": 0.83,
      "ocr_engine": "engine-name",
      "ocr_engine_version": "version-or-unknown",
      "ocr_config_id": "non-path configuration fingerprint",
      "representative_frame_ms": 13650,
      "crop_path": "crops/cue-<id>.jpg",
      "crop_sha256_local": "local-integrity-and-dedup-only",
      "crop_width": 960,
      "crop_height": 110,
      "duplicate_of_cue_id": null,
      "boundary_crops": []
    }
  ]
}
```

The example is a design aid, not a promise that current stats provide every field. Determine the representative frame from the actual OCR sampling path. Do not invent a frame association from cue timing alone. Record missing values as `null` or omit them; do not fabricate engine confidence or frame provenance. Persist no crop for a cue unless crop-to-cue association is defensible.

Store human confirmation as a separate, append-only review event, not by overwriting the OCR observation:

```json
{
  "event_id": "immutable-review-event-id",
  "cue_id": "same-run-cue-id",
  "task": "ocr_source_text",
  "status": "accepted",
  "verified_source_text": "text read from the crop",
  "reviewer": "user",
  "created_at": "ISO-8601 timestamp",
  "crop_sha256_local": "same-local-crop-digest"
}
```

Store translation review separately and link it to the accepted OCR event:

```json
{
  "event_id": "immutable-translation-event-id",
  "cue_id": "same-run-cue-id",
  "task": "translation_review",
  "source_review_event_id": "accepted-ocr-review-event-id",
  "source_language": "source language tag",
  "target_language": "target language tag",
  "verified_source_text": "confirmed source",
  "draft_translation": "current/generated target text",
  "suggested_translation": "review proposal or null",
  "approved_translation": "user-approved target text or null",
  "status": "pending|accepted|edited|rejected|deferred",
  "review_model": "provider/model or local",
  "prompt_version": "review instruction version",
  "glossary_version": "optional glossary version",
  "created_at": "ISO-8601 timestamp"
}
```

No translation record may be marked accepted without a linked accepted source-text review. Store rejected/deferred suggestions only if useful to the user and permitted by their local retention choice; do not include them in translation memory.

## AI review behavior and response contract

The review interface should send only the current cue crop, OCR text, SRT timing, language pair, and narrowly necessary neighboring context. Context is for meaning and continuity; the reviewer must not silently change neighboring cues or timing. Do not send a full episode SRT or video by default.

For each cue, the AI must:

1. Compare visible text in the supplied crop with `source_text_ocr`.
2. Return `source_status` as `confirmed`, `correction_proposed`, or `uncertain`, with a proposed source text only when justified by visible evidence.
3. If source status is not confirmed, set translation status to `deferred` and explain the uncertainty. Do not infer illegible words from context.
4. If source is confirmed, assess the draft translation for meaning, omissions, additions, terminology, and requested style. Return a proposal, never an automatic edit.
5. Preserve cue IDs and timestamps. Do not invent, merge, split, or retime cues in this review flow.
6. Return structured data that the UI can validate. Invalid or incomplete output is an error requiring review, not an accepted result.

Suggested response shape:

```json
{
  "schema_version": 1,
  "cue_id": "immutable-id-within-run",
  "source_review": {
    "status": "confirmed|correction_proposed|uncertain",
    "observed_text": "what the reviewer reads in the crop",
    "proposed_source_text": null,
    "reason": "short evidence-based explanation"
  },
  "translation_review": {
    "status": "reviewed|deferred",
    "issues": ["omission", "meaning", "term", "style"],
    "proposed_translation": null,
    "reason": "short explanation"
  }
}
```

The UI must show crop, source OCR, timing, draft translation, proposal, and reasons together. Acceptance is a separate explicit user action. A reviewer must not call a result “correct” merely because it is fluent.

## Privacy, consent, and resource controls

- Local processing and review are the default. No external AI call, telemetry request, upload, or background contribution is allowed by default.
- Before a remote provider receives content, show the exact fields and crops to be sent, provider/model, purpose, and applicable retention setting. Require explicit per-run or per-batch consent. Declining must leave local OCR and editing available.
- Never send full video by default. Never upload raw SRT, full-episode text, full-frame images, personal paths, usernames, or stable user/device identifiers by default.
- Subtitle crops and subtitle text can contain copyrighted material and private information. Treat them as sensitive. Public repositories and issue trackers are not a default dataset destination.
- If a future centralized contribution service is proposed, first define authentication, abuse controls, schema validation, rate/size limits, retention/deletion, access policy, consent revocation, and a way to inspect/retract submissions. Do not embed service secrets in GitHub Pages or a desktop client.
- Add explicit limits for crop dimensions, total crop count, export size, concurrent requests, model context size, and estimated API cost. Show estimates before an external review batch. Support cancel and resume without duplicating approved events.
- Keep the existing GPU default and explicit `--cpu` opt-in for product OCR. Do not make LLM review a dependency of OCR or block local SRT generation on an external service.

## Learning and application policy

Keep four artifacts distinct:

1. **OCR observation:** model text/confidence plus crop and frame provenance.
2. **Verified OCR label:** crop plus visually checked source text.
3. **Text correction rule:** optional deterministic `old -> new` post-processing rule, scoped as narrowly as possible and reviewed for collisions.
4. **Approved translation memory:** verified source text plus user-approved target text and optional terminology/style metadata.

Do not combine these stores. In particular:

- A translation pair must never be loaded by `_kullanici_uygula` as an OCR correction.
- An OCR correction rule must never be treated as an approved translation.
- An LLM-proposed source correction is not a verified OCR label until a person accepts it after viewing the crop.
- A correct OCR output is useful training data only when its image crop and provenance are available and a person confirms the text; accepting a correct translation contributes to translation memory only after source text is verified.
- Do not silently turn “Öğret” into model training. Keep its current deterministic replacement semantics clearly described unless a separately reviewed feature replaces it.
- Never auto-apply a new generalized OCR rule or train a production model from one sample. Start with candidate suggestions and reversible, versioned application.

## Measurement and release gates

### OCR loop metrics

Use trusted source-language subtitles, aligned to the relevant video interval, and hold out entire episodes/sources from training and tuning. Report at least:

- CER and WER, with normalization rules documented;
- exact-match cue rate;
- cue detection precision/recall or missed-cue recall, not only text quality on successfully detected cues;
- start/end timing error and the share within the documented tolerance;
- per-case results and macro average, as well as corpus-level micro aggregate;
- coverage, unavailable/ambiguous cases, and output/model/device versions.

Use the same device/backend/config when comparing a baseline and candidate, or report the difference explicitly. Do not choose thresholds after seeing the test result. A fixed regression corpus is a release gate, not proof of accuracy on all videos, fonts, encodings, or subtitle styles.

### Translation loop metrics

Evaluate only cues whose source text has been verified. Use a held-out set reviewed by bilingual humans with a consistent rubric covering semantic adequacy, omissions/additions, terminology consistency, and style. Report human acceptance/edit/rejection rates and issue categories. LLM self-scores may help triage but are not the sole success metric. Measure OCR and translation separately so an OCR change cannot appear to improve translation merely by changing the input set.

### Target and honest limits

A 95–99% target is meaningful only after naming the metric, corpus, languages, and supported video conditions. It may be a target for a defined held-out set, not a guarantee for arbitrary user videos. 100% cannot be promised: crops can be blurred/occluded, typography and compression vary, cues can be missed, source references can be misaligned, and both OCR and translation systems can make uncertain or context-sensitive errors. Show uncertainty and retain a user review path.

## Phased implementation plan

| Phase | Deliverable | Acceptance criteria | Stop conditions |
|---|---|---|---|
| 0. Baseline and scope | Source-backed inventory of current crop/frame path, stats, OCR regression cases, and separate OCR/translation metrics | Existing `regresyon/gt_gate.py` behavior and trusted references are documented; thresholds are predeclared; no new data upload | No trustworthy aligned source reference or no defensible crop-to-cue mapping: do not claim a measured gain or generate training labels |
| 1. Local crop manifest | Opt-in export of one compressed representative subtitle crop per unique cue plus manifest; deduplicated repeats; optional boundary crops | Immutable cue IDs; crop/frame/timing provenance; no full video or absolute paths; output is local and re-openable | Frame provenance cannot be established, export size exceeds configured cap, or dedup is ambiguous: mark unavailable and stop for those cues |
| 2. Human OCR verification | **Implemented locally on `codex/phase2-ocr-human-review`; not yet merged/runtime-validated.** Local review screen for crop + OCR text; append-only accepted/corrected/uncertain events and undo events | Only active human accepted/corrected visual labels enter the versioned OCR dataset export; uncertain labels remain excluded; undo/export implemented; no original SRT/manifest edits | No human acceptance, crop missing/mismatch, stale hashes, non-exact mapping, or malformed record: exclude from dataset |
| 3. Translation review pilot | Explicitly invoked AI review on a selected batch, with crop/source-first order and structured proposals | No call without consent; uncertain OCR defers translation; user can accept/edit/reject; only accepted pairs enter translation memory | Consent absent, provider unavailable, schema invalid, or source unverified: do not send/store as accepted |
| 4. Scoped lexicon and translation memory | Separate versioned stores and retrieval/application rules | OCR rule does not affect translation memory; translation memory does not affect OCR post-fix; collision tests and rollback record exist | Generic rules cause held-out regressions or source/target linkage is missing: disable the rule/item |
| 5. Offline OCR experiment | Reproducible experiment using verified crop/text examples; candidate model artifact kept separate | Train/validation/test split by episode/source; GPU-default product behavior unchanged; report CER/WER/detection/timing against same baseline; no held-out regression beyond predeclared limits | Dataset too small/imbalanced, labels uncertain, licensing unclear, or candidate fails gate: do not ship model |
| 6. Controlled release | Versioned local package/model and migration notes; opt-in sharing considered separately | Model provenance, rollback, supported environments, consent and privacy checks documented; user can continue CPU or existing model path | Any unreviewed upload, secret in client, silent model/rule change, or reproducible regression: stop release |

Implement one phase at a time. Do not bundle model training, remote API, and translation UI into the first change.

### Phase 2 implementation notes

- Open the local UI and visit **Yerel OCR Doğrulama** at `http://127.0.0.1:8765/ocr-inceleme`; select a `.review-pack` folder with the Windows folder dialog or enter its path.
- For each crop, choose **Görüntü doğru**, enter a visually confirmed source transcription and choose **Düzeltmeyi kaydet**, or choose **Belirsiz**. The browser is only a local UI; there is no AI/provider call or upload.
- The append-only `review-events.jsonl` lives inside that review pack. A decision carries a random event ID, cue/job identity, manifest/SRT/crop SHA-256 hashes, status, verified source text (empty for uncertain), and timestamp. Undo appends an event referencing the current decision; it never deletes or rewrites earlier events.
- The viewer rechecks the manifest and copied SRT hash, every SRT cue's order/timing/text, unique cue IDs, exact one-to-one OCR mapping, safe crop path, and crop digest before displaying or recording. If those no longer match, the item cannot be accepted. Export rechecks them and writes a new immutable `verified-ocr-dataset-<UTC>-<id>/` snapshot containing only crop images and `manifest.jsonl`; it does not include the SRT.
- The dataset is a local snapshot and is not automatically updated after later reviews or undo events. Create a new export for a new active decision state. Old exports can be removed manually after confirming they are no longer needed; review events remain the source history.
- No browser or file-picker runtime test was run during this phase. Static validation and manual code review status are reported in the implementation handoff; a real synthetic pack acceptance/undo/export smoke check remains outstanding before treating the UI flow as verified.

## Required artifacts for each implementation phase

The implementing agent must deliver:

- a short current-state and change summary with exact files;
- the data-flow/schema and a small synthetic example containing no user media or real subtitle text;
- explicit consent and storage behavior for every content-bearing operation;
- metric definitions and baseline/candidate results when behavior changes;
- static review of path containment, deduplication, ID stability, malformed records, retries, cancellation, and failure visibility;
- a statement of checks run, checks not run, and known limits;
- reversible migration or rollback instructions for stored data and rules.

Do not report an improvement from counts of corrections alone. A higher correction count can mean either better review coverage or worse OCR/translation quality.

## Paste-ready task brief for a future AI agent

> Implement the next approved phase in `docs/LEARNING-SYSTEM-PLAN.md`. First inspect the current branch and relevant code/docs; do not assume this plan's current-state notes are still true. Report the files and data flow you intend to change before a broad edit. Keep visual OCR source-text learning separate from AI translation review and translation memory. Use only visually verified crop + source-text pairs as OCR labels, and only user-approved translations linked to verified source text as translation memory. LLM outputs are proposals; never auto-train or auto-apply them. Keep processing local by default, require explicit review and consent before any external call or upload, and never upload full video/raw SRT by default. Preserve GPU as the default when supported and keep `--cpu` opt-in. Do not run OCR over the user's library. Implement one phase only, use synthetic fixtures for examples, report metrics and limitations, and stop if crop provenance, ground truth, consent, or privacy requirements cannot be satisfied.

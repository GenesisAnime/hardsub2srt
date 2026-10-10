# OCR and AI translation learning system: implementation brief

**Status:** Phases 0–1 have local artifacts. Phase 2 human OCR review is present in the current source branch but has not had a browser/runtime acceptance check. Phase 3 has a provider-agnostic manual AI request/response flow; it makes no provider calls and performs no upload. Phase 4 adds a local, scoped translation memory and terminology lexicon with explicit draft application and append-only rollback. Phase 4 has not had a runtime acceptance check. Phase 5 has a local dataset-preparation gate and report contract, but no training/evaluation adapter or trusted licensed corpus; no experiment has run and no model was trained. **Phase 6 has only a fail-closed validation scaffold (`GATE_SCAFFOLD_ONLY`, blocked): there is no eligible evaluated Phase 5 PASS report, trusted rights-review receipt, or candidate model. It cannot package or publish.** The user confirmed AI review stays manual; there will be no provider API integration in this work. See [Phase 5 offline experiment contract](OCR-OFFLINE-EXPERIMENT.md) and [Phase 6 controlled release gate](CONTROLLED-MODEL-RELEASE.md).

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
| 2. Human OCR verification | **Implemented in the current source branch; browser/runtime acceptance check remains outstanding.** Local review screen for crop + OCR text; append-only accepted/corrected/uncertain events and undo events | Only active human accepted/corrected visual labels enter the versioned OCR dataset export; uncertain labels remain excluded; undo/export implemented; no original SRT/manifest edits | No human acceptance, crop missing/mismatch, stale hashes, non-exact mapping, or malformed record: exclude from dataset |
| 3. Translation review pilot | **Manual AI round-trip implemented locally.** User exports a selected, human-source-verified batch, submits it to an AI outside the app, then imports the structured response. No provider API call or upload. | Strict request/response schema, source-first deferral, explicit accept/edit/reject, append-only proposal/decision history, accepted-only separate translation memory. Direct provider API integration is out of scope per the user's manual-review preference. | No manual user sharing, provider/API consent absent, provider unavailable, schema invalid, stale pack, or source unverified: do not send/store as accepted |
| 4. Scoped lexicon and translation memory | **Local implementation added.** Separate versioned translation and lexicon projections; exact scoped retrieval; explicit application to a draft; append-only rollback | OCR rule does not affect translation memory; translation memory does not affect OCR post-fix; source/target scope is explicit; conflicts are surfaced as ambiguous; rollback is recoverable | Generic rules cause held-out regressions or source/target linkage is missing: disable the rule/item. Automated/runtime checks remain outstanding |
| 5. Offline OCR experiment | **Preparation gate/report contract implemented** in `ocr_experiment.py`: checks Phase 2 `ocr_confidence`, crop hashes/dimensions/decode, and (when linked) active parent review events; requires per-cue source/episode and hash-bound rights evidence; creates deterministic group splits plus a null-metric report. Provenance that cannot be linked is marked self-attested and blocked; rights always remain `rights_verified:false` pending human legal review. The train/eval adapter, trusted timed reference corpus, actual metrics, and candidate model are **not implemented**. | Once authorized/licensed data and an engine adapter exist: train/validation/test split by episode/source; GPU-default product behavior unchanged; report CER/WER/detection/timing against same baseline; no held-out regression beyond predeclared limits. The tool reports row and group counts separately and does not claim evaluation. | Dataset too small/imbalanced, labels uncertain, rights evidence absent or human review incomplete, no aligned trusted reference, missing engine/config provenance, or candidate fails gate: do not train/ship/model-claim |
| 6. Controlled release | **`GATE_SCAFFOLD_ONLY` — blocked.** Added strict candidate/report/rights receipt schemas and a fail-closed local validator. It verifies candidate file hashes, binds a future Phase 5 v2 PASS to the exact weights/recipe/engine/source commit, parses a scoped human rights receipt, and requires pinned per-OS/Python/backend/device validation evidence. All trust registries are empty. Current Phase 5 v1 (`PREPARED_NOT_RUN`, blocked, rights false) is rejected unconditionally. It never packages or publishes. | Once all prior gates genuinely pass: signed-off rights/provenance review; same frozen held-out test set demonstrates predeclared CER/detection/timing/coverage gates; candidate file hashes, compatibility matrix, migration and rollback are verified; GPU stays default and CPU remains opt-in; activation requires explicit user confirmation and is reversible. | Any unreviewed upload, untrusted/self-attested rights claim, missing evaluation PASS, hash mismatch, unsupported runtime/device, silent model/rule change, or reproducible regression: block release |

Implement one phase at a time. Do not bundle model training, remote API, and translation UI into the first change.

### Phase 2 implementation notes

- Open the local UI and visit **Yerel OCR Doğrulama** at `http://127.0.0.1:8765/ocr-inceleme`; select a `.review-pack` folder with the Windows folder dialog or enter its path.
- For each crop, choose **Görüntü doğru**, enter a visually confirmed source transcription and choose **Düzeltmeyi kaydet**, or choose **Belirsiz**. The browser is only a local UI; there is no AI/provider call or upload.
- The append-only `review-events.jsonl` lives inside that review pack. A decision carries a random event ID, cue/job identity, manifest/SRT/crop SHA-256 hashes, status, verified source text (empty for uncertain), and timestamp. Undo appends an event referencing the current decision; it never deletes or rewrites earlier events.
- Opening a pack checks the manifest/SRT, every SRT cue's order/timing/text, unique cue IDs, exact one-to-one OCR mapping, and safe crop paths. Crops are validated lazily when requested by the page; only decodable JPEG crops up to 1280×1280, 8 MiB each, and 128 MiB total are eligible. Browser image-load failure disables decisions. The pack is capped at 5,000 cues and 16 MiB each for manifest and SRT. Each human decision re-hashes the manifest and SRT and validates the selected crop; export repeats the source checks and validates every exported crop. Changed or stale content cannot enter a label. Export writes a new immutable `verified-ocr-dataset-<UTC>-<id>/` snapshot containing only crop images and `manifest.jsonl`; it does not include the SRT.
- Review JSONL events use exact schema version 1 with UUID-format event/run IDs, cue identity, source/crop hashes, reviewer marker, ISO-8601 timezone timestamp, and status-specific fields. Invalid schemas and stale hashes remain in the append-only log but never become active labels. At most eight review sessions are held in memory; event logs are streamed with a 64 MiB, 50,000-record, and 256 KiB-per-record cap. If the record limit is exceeded, the reader fails closed and produces no active decisions or dataset from a truncated prefix. Status uses a bounded session cache; undo's event read and append are serialized within the local process.
- The dataset is a local snapshot and is not automatically updated after later reviews or undo events. Create a new export for a new active decision state. Old exports can be removed manually after confirming they are no longer needed; review events remain the source history.
- No browser or file-picker runtime test was run during this phase. Static validation and manual code review status are reported in the implementation handoff; a real synthetic pack acceptance/undo/export smoke check remains outstanding before treating the UI flow as verified.

### Phase 3 implementation notes — manual AI round-trip

- The local page at `/ocr-inceleme` now has an **AI çeviri incelemesi** section. It only offers cues with an active human accepted/corrected source-text event and a currently valid crop. Enter the draft target translation, select the cue, and download the generated JSON request.
- The app does not contact GPT, DeepSeek, or another provider. The primary **Sohbet için görselli ZIP indir** action creates a ZIP with `prompt.md`, metadata-only `request.json`, an import-compatible `response-template.json`, and separate `images/<cue-id>.jpg` files. The user unzips it and manually attaches all four parts to a multimodal AI chat: `prompt.md`, `request.json`, `response-template.json`, and the selected images. The response template carries the exact output schema and cue/time mapping. The user then pastes the JSON response into the local page. A legacy base64 JSON export is also available. The shareable package omits absolute paths, crop/SRT/manifest hashes, source-review event IDs, and device/account identifiers. A private local sidecar stores the cue/hash/source-event mapping needed to reject stale responses; do not share the sidecar or the app's internal request folder.
- Each response must preserve request ID, cue IDs, and exact timings and match the strict structured schema. Source status must be `confirmed`, `correction_proposed`, or `uncertain`; a non-confirmed source is only valid with a deferred translation and no translation proposal. Invalid, duplicate, stale, or mismatched responses are rejected or excluded. Re-importing the same response is idempotent.
- Imported AI results are append-only `proposal` events in `translation-review-events.jsonl`, separate from OCR events. The user must explicitly **accept**, **edit and accept**, or **reject** each proposal. A translation cannot be accepted while the AI marks the source uncertain/corrected or translation deferred. The append-only decision event is the source of truth. `translation-memory-v1.jsonl` is an atomic, rebuildable snapshot derived from valid accepted/edited decisions; it links each target to the human-verified source event. If snapshot writing fails, the UI reports that the decision was saved and offers retry; reopening the page also retries projection. These files never feed `/api/ogret`, `ogren.py`, `kullanici-sozlugu.txt`, OCR labels, or the OCR model.
- The AI request contains subtitle crops and text. Sending the downloaded request to a provider is a user-controlled disclosure; review the file/provider retention policy first. No automatic API integration, per-provider consent UI, server upload, background telemetry, batch cost estimate, deletion UI, or model training is implemented. The user chose to keep this AI step manual; it is not pending a provider-API decision.
- Limits: at most 100 cues per request or ZIP, 12 MiB per request/ZIP, 4 MiB per imported response, 20,000-character fields, and bounded append-only event files. Crop/base64 request size is estimated and checked one cue at a time before reading and encoding that image, keeping accumulated image payload within the configured cap. ZIP creation revalidates source links and emits each JPEG as a separate file; the private sidecar is never added to the ZIP. `translation-memory-v1.jsonl` is local to the review pack, not a global glossary. Rejected proposals stay in local event history but never enter translation memory. The proposal UI has not had a browser/runtime test; no provider request or video was run as part of implementation.

### Phase 4 implementation notes — scoped local memory

- The Phase 4 section is inside `/ocr-inceleme`. The user enters a project/series key and source/target language tags. The canonical scope is the exact tuple `(project_key, source_language, target_language)`; project keys are case-insensitive ASCII/Unicode word characters plus `.`, `_`, `-`, and language tags are normalized to lowercase hyphen form. There is no implicit global or cross-language fallback.
- Windows data lives under `%LOCALAPPDATA%\hardsub2srt\translation-memory-v1`; on other platforms it uses `$XDG_DATA_HOME/hardsub2srt/translation-memory-v1` or `~/.local/share/...`. Files are local-only, mode 0600 where supported. The app does not return this path to the page and makes no network call. Distinct scope IDs use SHA-256 over the canonical tuple.
- One append-only, bounded event journal stores explicit additions and rollback events. Atomic rebuildable `*-translation-memory-v1.jsonl` and `*-lexicon-v1.jsonl` projections remain separate. Translation additions are admitted only by selecting an existing Phase 3 accepted/edited decision whose source event and crop still pass the Phase 3 review-pack validation; the record carries the verified source review event ID and translation decision event ID. They never update OCR data or `kullanici-sozlugu.txt`.
- The user explicitly saves selected approved translations to a scope. A memory hit requires a normalized exact source-text match (Unicode NFKC, case-folding, collapsed whitespace) in the exact scope. Multiple distinct approved target strings for that source return `ambiguous`; none is silently selected. A unique hit is displayed as a suggestion and enters the translation draft only after **Taslağa al**.
- Lexicon entries are explicit user-entered source/target terms, separate from accepted translation pairs. Retrieval uses literal case-insensitive substring matches (including Japanese/Chinese text, where Unicode `\w` boundaries can hide valid terms) and does not call or train a model. It enumerates overlapping occurrences; overlapping replacements are `ambiguous`. Since no trusted CJK tokenizer is present, any CJK-script match touching another adjacent CJK-script character is also `ambiguous`, not a suggestion. Conflicting targets return `ambiguous`; otherwise a candidate draft is shown. The text is changed only when the user presses **Terimleri taslağa uygula**. This simple phrase matcher is not morphological analysis and should be treated as a candidate generator.
- **Geri al** appends a rollback event referencing the add-event ID. It never deletes old history; repeating rollback is safe/idempotent. Rebuilding the two projections excludes rolled-back entries. A later explicit add creates a new event and can re-enable an equivalent pair.
- Limits: one scope is capped at 50,000 events, 32 MiB, and 128 KiB per event line; a bulk import accepts up to 500 explicit accepted decision IDs. Invalid/truncated history fails closed rather than returning a partial memory. Scope collisions are checked by retaining the full scope tuple in every event, not only trusting its hash.
- The `/api/ceviri-bellegi/` routes accept only `Host: localhost:<configured-port>` or `127.0.0.1:<configured-port>`. Mutating requests must also have a matching same-origin HTTP `Origin` (or same-origin `Referer` fallback); other hosts, ports, and origins are rejected to reduce DNS-rebinding and cross-site request risks. Both loopback aliases remain supported when the page is opened through the same alias.
- No tests were added or run in this phase by instruction. Static Python compilation and source review are reported separately. Browser/runtime testing with a synthetic review pack remains necessary. The AI request/response remains manual: no GPT/DeepSeek API, upload, telemetry, OCR, or video processing was run.

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

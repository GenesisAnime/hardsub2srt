# Phase 5 — local OCR experiment preparation and evaluation contract

## Current state

`ocr_experiment.py` locally validates the Phase 2 `verified-ocr-dataset-*/manifest.jsonl` export and its separate metadata file. For valid inputs it creates `evaluation-plan-v1.json` before measurement, followed by a hash-bound `split-manifest.json` and `experiment-report.json`. It does not run OCR, train a model, modify the dataset, or access the network. Metadata, plans, splits, and reports are sensitive local files, not shareable packages.

Phase 2 exports contain only valid `accepted`/`corrected` decisions. The tool revalidates the `.review-pack` parent, `manifest.json`, SRT, and `review-events.jsonl` chain; it checks that an event is the active final decision and that event/cue/hash/text/time/confidence values match. If this chain cannot be verified, output provenance is `self_attested` and `experiment_run_blocked` remains true. Even a valid local link proves only linkage to the local UI journal, not the identity of an account/person. AI proposals are excluded from exports.

### Merge exports from multiple review packs

The UI creates a separate verified export for each `.review-pack`. Phase 5 requires at least three independent source+episode groups, so do not merge exports manually by copying rows/crops. `ocr_dataset_merge.py` combines validated exports with `source-lineage-v1.json`, which links every cue to its source export and active review event. The combined directory copies only export manifest rows and validated crop bytes; it does not write to source review packs/exports.

The common root must already exist and be local. Source review packs and the combined export must be beneath it; source paths are relative to that root. The output is a new subdirectory under the same root:

```powershell
py -3 ocr_dataset_merge.py `
  --root "C:\LOCAL-ONLY-PLACEHOLDER\local-review-root" `
  --source-export "run-01\synthetic.review-pack\verified-ocr-dataset-PLACEHOLDER" `
  --source-export "run-02\synthetic.review-pack\verified-ocr-dataset-PLACEHOLDER" `
  --source-export "run-03\synthetic.review-pack\verified-ocr-dataset-PLACEHOLDER" `
  --out-name "verified-ocr-dataset-composite-SYNTHETIC"
```

These are synthetic paths. All cues in each source pack must map in metadata to one real source+episode group, and different packs must map to different real groups. Merge stops on cue collisions, duplicate packs, source export hash mismatches, inactive decisions, crop mismatches, paths outside the root/reparse paths, or total-size limits. `source-lineage-v1.json` binds the source export manifest and parent review manifest, SRT, and event journal hashes; the validator rechecks them during every preparation/evaluation call. Metadata, rights evidence, and human rights review are still required; merge does not create or approve them.

Create an empty metadata template for the combined export with the CLI. The review UI's template button is tied to an export inside one open review pack:

```powershell
py -3 ocr_dataset_metadata.py `
  "C:\LOCAL-ONLY-PLACEHOLDER\local-review-root\verified-ocr-dataset-composite-SYNTHETIC" `
  --out "C:\LOCAL-ONLY-PLACEHOLDER\local-review-root\dataset-metadata.template.json"
```

Identity, license, and rights-review fields in the template are blank/pending. The user must fill them from the real material and reviewed evidence. A template, evidence file, or `human_review` marker does not grant legal rights.

## Metadata input

### Create an empty metadata template

After human acceptance/correction of Phase 2 decisions, create only a starter template if source/episode IDs or rights status are unknown:

```powershell
py -3 ocr_dataset_metadata.py "D:\local\review-pack\verified-ocr-dataset-..." `
  --out "D:\local-metadata\dataset-metadata.template.json"
```

The `--out` directory must already exist; the target JSON must not exist and must be outside the verified dataset. The tool checks the Phase 2 manifest schema, cue IDs, crop hash/decode integrity, and linkage to active human decisions in the parent review pack. If linkage cannot be verified, no file is created. The template contains cue IDs only; it contains no SRT/source text or absolute paths.

The generated file leaves `source_id`, `episode_id`, license details, and rights evidence blank; `human_review.status` remains `pending`. No identity, license, or usage right is inferred. The file intentionally fails required experiment-validator fields and cannot be used for Phase 5 preparation until completed and human-reviewed. The file or an evidence attachment is not proof of legal permission. This command is a convenience tool, not a dataset or rights approval.

Each `cue_id` requires `source_id`, `episode_id`, and license fields. License status must be one of `public_domain`, `permissive_license`, `permission_granted`, or `user_owned`. Each record requires a metadata-relative `evidence_path` and SHA-256; file existence and hash are checked. `human_review` may record a reviewer/date, but neither the evidence file nor this field is **legal verification**; reports always say `rights_verified:false`. The experiment gate stays closed until human rights review is complete. Stop if the license is uncertain.

Example `dataset-metadata.json`:

```json
{
  "schema_version": 1,
  "records": [
    {
      "cue_id": "cue-id-from-export",
      "source_id": "series-or-source-stable-local-id",
      "episode_id": "episode-stable-local-id",
      "license": {
        "status": "permission_granted",
        "identifier": "permission-record-2026-001",
        "evidence_path": "rights/permission-record-2026-001.txt",
        "evidence_sha256": "<64-lowercase-hex-digest-of-file>",
        "human_review": {
          "status": "pending",
          "reviewer": null,
          "reviewed_at": null
        }
      }
    }
  ]
}
```

## Preparation

Choose and freeze values before seeing results: split seed, baseline/candidate definitions, CER regression limit, timing tolerance, and **minimum coverage for each test source+episode group**. There is no project default or universal percentage; define the rate in advance for your supported scope. `0.90` below is only an example. Provide a new, empty `--out` directory:

```powershell
py -3 ocr_experiment.py "D:\local\verified-ocr-dataset-..." `
  --metadata "D:\local\dataset-metadata.json" `
  --out "D:\local\experiment-2026-01" `
  --seed "phase5-v1-frozen" `
  --baseline "current-engine-config-id" `
  --candidate "candidate-engine-config-id" `
  --max-cer-regression 0.002 `
  --min-test-group-coverage 0.90 `
  --timing-tolerance-ms 500
```

Preparation writes `evaluation-plan-v1.json` before measuring results. The plan freezes the dataset manifest, metadata, split seed, engine IDs, threshold, and coverage definition. The split and `experiment-report.json` contain the plan's raw-byte SHA-256. The evaluator stops if the plan is missing/changed or copied from another experiment directory. Do not edit old files to change a threshold; create a new experiment directory and plan. This hash binding is an integrity check between local files, not a digital signature or externally verified trust pin. It detects plan changes against the existing split/report hashes, but does not prevent rewriting the plan, split, and report together and recalculating hashes. It does not create a trusted timestamp, signature, or rights approval.

### Older Phase 5 preparation files

`schema_version=1` `PREPARED_NOT_RUN` reports created before the coverage plan lack plan/split hashes and `evaluation-plan-v1.json`. The current validator deliberately rejects those old directories. Do not edit old files in place to add hashes or thresholds. Rerun `ocr_experiment.py` with an explicitly selected coverage rate in a **new, empty `--out` directory**. Keep the old report as a historical record. The schema ID remains v1, but old preparation artifacts do not satisfy the current v1 validator contract.

The command checks that crop paths are relative and remain within the root, SHA-256 values match, JPEG size markers are within safe bounds, and OpenCV can decode each image. A crop is limited to 1280×1280; unique crops total at most 128 MiB; metadata is limited to 16 MiB, each rights evidence file to 16 MiB, and all evidence files together to 64 MiB. It rejects duplicate `cue_id`s, an invalid Phase 2 manifest schema (including `ocr_confidence`), missing/extra metadata rows, empty human-verified text, missing license evidence/hash, and corrupt crops. `review_event_id` alone is insufficient: it must link through the parent `.review-pack` and `review-events.jsonl` to the still-active decision and matching cue/crop/hash/SRT/time/text/confidence values. Without this chain, provenance stays self-attested and the experiment is blocked. Group keys combine `source_id + episode_id` after NFC normalization, whitespace collapsing, and casefolding. Cues from one group never cross splits. Output is stopped if there are fewer than three groups or any split would be empty.

Deterministic ordering uses `SHA256(seed + NUL + canonical_group_id)`; sorted groups are assigned to fixed 80/10/10 target group counts. This splits by group count and does not guarantee an exact 80/10/10 split by sample/cue count. With few groups the ratios are approximate; the report gives group and cue-row counts separately. As data is added, do not silently change existing test groups: freeze the experiment manifest and start a new experiment version.

## Metric definitions and report

Machine-readable schemas are `schemas/ocr-experiment-metadata-v1.schema.json`, `schemas/ocr-experiment-split-v1.schema.json`, `schemas/ocr-evaluation-plan-v1.schema.json` for the new frozen plan, `schemas/ocr-experiment-report-v1.schema.json`, `schemas/ocr-video-evaluation-report-v1.schema.json` for video reports, and `schemas/ocr-crop-evaluation-report-v1.schema.json` for crop-only reports. During preparation, `experiment-report.json` always has `PREPARED_NOT_RUN`, `rights_verified:false`, `experiment_run_blocked:true`, and null metrics. Null means unmeasured, not zero success. Rights-file existence/hash only links an evidence file to a self-declared record; it does not verify legal permission. `ocr_crop_eval.py` measures only still-text CER/WER/exact match/coverage. `ocr_video_eval.py` is a separate measurement-only adapter for fixed SRT and timed reference files; it does not run real video/OCR or produce release PASS. `vtt-qa.py` and `regresyon/gt_gate.py` remain separate existing regression tools.

The `ocr_crop_eval.py` still-crop adapter measures OCR **crop text** against Phase 2 human-approved crops and source text. The metrics fall into two groups:

- Measurable from crop text: CER, WER, exact match, and coverage.
- Not measurable from fixed crops: cue detection/precision/recall and timing.

### Run crop-level text measurement

First create `split-manifest.json` using the preparation steps above. Every record in preparation metadata must have `license.human_review.status` set to `reviewed`. This indicates only that the user attests to a rights review; reports still always say `rights_verified:false`. The program does not verify usage rights. The parent review-pack/event chain is revalidated before every engine starts; a moved/self-attested export is rejected.

```powershell
py -3 ocr_crop_eval.py "D:\local\verified-ocr-dataset-..." `
  --metadata "D:\local\dataset-metadata.json" `
  --split "D:\local\experiment-2026-01\split-manifest.json" `
  --out "D:\local-reports\crop-eval-2026-01.json" `
  --engine easyocr-single `
  --engine easyocr-consensus3 `
  --engine rapidocr-v6-small `
  --rapid-det-model "D:\models\det.onnx" `
  --rapid-rec-model "D:\models\rec.onnx" `
  --rapid-dict "D:\models\dict.txt"
```

For RapidOCR, det/rec ONNX files and the dictionary paths are required, local, and must exist. EasyOCR uses only an existing model directory (default `~/.EasyOCR/model`, configurable with `--easyocr-model-dir`) and is configured with `download_enabled=False`. A missing model is an error; no weights are downloaded. GPU is the default when available; `--cpu` explicitly selects CPU. Reports include engine version, config summary, EasyOCR reader's configured device, and configured provider lists for created ONNX sessions. These are not node-level inference profiles. Processing stops if the configured provider list cannot be read from a RapidOCR session.

Every engine processes the same frozen `test` split rows. Immediately before each engine, the manifest, metadata, crop SHA/decode, split assignment, and active Phase 2 decision chain are revalidated. Output contains aggregate metrics only; it does not write crops, OCR output, human source text, or cue IDs. Write a new JSON report outside the dataset/review-pack/metadata directories.

- CER/WER reference is source text visually approved by a human. Metric normalization applies NFC and whitespace collapse/trim while preserving case and punctuation.
- Exact match is the number of matching normalized strings divided by test rows.
- Coverage is non-empty OCR outputs / total test crop rows. Empty output lowers coverage and counts as an empty prediction for edit metrics.
- `micro` is total edits / total reference units across all test rows; `group_macro` is the simple mean across source+episode groups. Aggregate values are also reported per source+episode.
- Each still crop is already an image of a cue. Therefore this adapter does not measure video cue detection or timing; those fields remain `null`. This is **not video accuracy**, is not a Phase 6 `PASS`, and does not open the release gate. It does not change the preparation `experiment-report.json`; it uses the separate `schemas/ocr-crop-evaluation-report-v1.schema.json`.

Output and model identities are sensitive local data. Reports and metrics are not automatically sent over the network. No model training occurs. This adapter does not replace time-aligned video-reference evaluation by `vtt-qa.py` or `regresyon/gt_gate.py`.

## Full-episode SRT measurement adapter

### Empty manifest drafts for eligible Phase 5 inputs

`ocr_video_eval_templates.py` creates local drafts only for dataset/metadata/experiment/split inputs that pass existing strict Phase 5 validation. Before writing, it calls `_validated_split` and `ocr_crop_eval.validate_run`, as the real adapter does. It writes no files if the Phase 2 active decision chain, rights evidence/review metadata, hashes, or frozen group split cannot be validated. This helper does not weaken validation.

```powershell
py -3 ocr_video_eval_templates.py "D:\local\verified-ocr-dataset-..." `
  --metadata "D:\local\dataset-metadata.json" `
  --experiment "D:\local\experiment-...\experiment-report.json" `
  --out-parent "D:\phase5-drafts" `
  --protect-dir "D:\local\future-references" `
  --protect-dir "D:\local\future-predictions"
```

`--out-parent` must already exist, be local, and not be synchronized. UNC/mapped drives and paths beneath roots in `OneDrive`, `OneDriveCommercial`, or `OneDriveConsumer` environment variables are rejected. It must be outside the dataset, its review-pack parent, and metadata/experiment/split directories. Add every future reference/prediction input directory to the extra protection list with `--protect-dir`. The helper creates a unique child directory; it stages two draft JSON files and a README, then moves them atomically. Existing output is never overwritten.

`timed-reference-manifest-v1.draft.json` and `prediction-manifest-v1.draft.json` use the special [`draft-v1 schema`](../schemas/ocr-video-evaluation-templates-draft-v1.schema.json). This format intentionally differs from manifests accepted by `ocr_video_eval.py`: `schema_version="draft-v1"`, `template_only=true`, and `INCOMPLETE_NOT_FOR_EVALUATION` cause rejection as real evaluation input. Only frozen source/episode/split values and baseline/candidate IDs are filled in. Language, media hash, SRT paths/hashes, alignment review (always `pending`), model/config hash, device, and backend are deliberately blank. Drafts do not create rights approval, trusted references, evaluation results, or Phase 6 PASS. Successful synthetic tests mock the validator boundary; the real strict validator also rejects empty dataset/rights inputs without writing output.

`ocr_video_eval.py` is a **measurement adapter** for comparing fixed full-episode SRT outputs once permitted, human-alignment-reviewed source-language references exist. It does not open video, run OCR/model, train, or access the network. By itself it does not verify that an OCR engine ran with the stated video/model/device. In the legacy manual `prediction-manifest-v1.json` format, the caller declares engine, config hash, `configured_device`, and `session_configured_backend`; legacy `device`/`backend` fields are read for compatibility. Runner-produced manifests keep the same v1 filename but also include an `execution_receipt` binding. Neither format is independent proof of hardware use.

Example (the preparation files must first be created by `ocr_experiment.py`):

```powershell
py -3 ocr_video_eval.py "D:\local\verified-ocr-dataset-..." `
  --metadata "D:\local\dataset-metadata.json" `
  --experiment "D:\local\experiment-...\experiment-report.json" `
  --references "D:\local\timed-references\timed-reference-manifest-v1.json" `
  --predictions "D:\local\fixed-outputs\prediction-manifest-v1.json" `
  --out "D:\local-reports\video-eval-2026-01.json"
```

Input formats are strict and documented in `schemas/ocr-timed-reference-manifest-v1.schema.json` and `schemas/ocr-prediction-manifest-v1.schema.json`. Each contains exactly one row per frozen `source_id + episode_id` group; the prediction manifest has one row for each prepared baseline and candidate. Their split assignment must match the existing `split-manifest.json`. The helper first calls `ocr_crop_eval.validate_run`, rechecking the active Phase 2 event chain, metadata/evidence hashes, human rights-review attestation, and existing frozen split. It does not invent a replacement split contract. It also requires the exact sibling `split-manifest.json` and a `PREPARED_NOT_RUN` experiment report.

Timed-reference record example:

```json
{
  "schema_version": 1,
  "records": [{
    "source_id": "source-local-id",
    "episode_id": "episode-local-id",
    "split": "test",
    "language": "ja",
    "media_sha256": "<64 lowercase hex of exact media file>",
    "reference_srt_path": "references/episode.srt",
    "reference_srt_sha256": "<64 lowercase hex>",
    "alignment_review": {
      "status": "reviewed",
      "reviewer": "human reviewer marker",
      "reviewed_at": "2026-10-11T12:00:00Z",
      "media_sha256": "<same 64 lowercase media hash>"
    }
  }]
}
```

Prediction records use the same `source_id`, `episode_id`, `split`, and `media_sha256`, plus `engine_id` (exactly the frozen baseline/candidate IDs), `prediction_srt_path`, `prediction_srt_sha256`, `model_sha256`, `config_sha256`, `configured_device` (`gpu` or `cpu`), and `session_configured_backend`. Put relative SRT files under their respective manifest directories. `prediction-manifest-v1` must contain both engine outputs for every split group; engine config/configured-device/session-configured-backend must remain constant across groups for a given engine. These fields bind the provided files and declare how they were produced, but this fixed-output adapter cannot verify execution.

Each timed-reference row binds a local relative SRT path and SHA-256 to source, episode, split, and media SHA-256. `alignment_review.status=reviewed`, reviewer, timezone timestamp, and the same media hash are required. This is a human attestation, not independent proof that the subtitle is authorized, source-language, synchronized, or reviewed by a verified person. Rights remain `false` in every report. Each prediction row similarly binds a local SRT and SHA-256 to the exact media hash plus self-declared engine config/configured-device/session-configured-backend.

Every referenced path must remain beneath its manifest directory; hash or group mismatch stops evaluation. Input SRTs must be strict UTF-8 and within size/cue-count limits; every non-empty block must parse, and malformed blocks are not silently skipped. One report requires the same `language` value in every timed reference; mixed-language references are rejected so WER tokenization is not pooled across incompatible languages. The current dataset metadata schema has no language field for comparison, so the reference language remains a human declaration and is included in the report.

Cues use deterministic maximum-cardinality one-to-one temporal matching. Candidate pairs require intersection / **reference cue duration** >= 0.5. Hopcroft-Karp traversal orders reference cues by candidate degree then file order; each adjacency list prefers larger overlap, then prediction file order. This maximizes the number of valid pairs. Overlap ordering is a stable tie-break preference; it does not claim globally maximal total overlap among all maximum-cardinality matchings. To bound dense interval work, a shared counter allows at most **2,000,000 total pair operations**: each prediction visited while pruning the active list and each remaining reference/prediction overlap comparison count once. The adapter also retains at most **2,000,000 qualifying edges**. Each bound is checked before processing/appending the over-limit item. Either limit stops scoring, even if all overlaps are below threshold. Text CER/WER and normalization reuse `ocr_crop_eval.py`; unmatched references count as empty OCR text, while extra prediction cues lower precision. Timing reports mean absolute start/end error for matched cues and the share whose two errors are within the previously frozen tolerance. Reports include train/validation/test, micro, and per-group results, but never cue text, raw source/episode IDs, or absolute paths (group rows use SHA-256 pseudonyms).

The output schema `schemas/ocr-video-evaluation-report-v1.schema.json` is deliberately **not** the Phase 6 input schema. It records the candidate's observed test CER delta, plan hash, and per-test-group coverage for both baseline and candidate, but does not turn either comparison into release eligibility. Coverage is `matched_cues / reference_cues` within each frozen source+episode test group. A match is the evaluator's deterministic one-to-one temporal match requiring overlap / reference-cue duration >= 0.5. Unmatched reference cues remain in the denominator; extra predictions do not increase it. Both engines must meet the explicitly predeclared threshold for each group. A group exactly at threshold passes; one below it makes the coverage gate `FAIL`. The overall comparison also fails if candidate corpus CER exceeds the frozen limit, or if any test group's candidate CER regresses while the frozen no-group-regression rule is required. A failed comparison returns exit code 1 after saving its measurement report. Missing, duplicate, zero-denominator, inconsistent, or unexpected test-group coverage stops evaluation without writing a report. Status is always `MEASURED_ADAPTER_ONLY_NOT_RELEASE_PASS`, `phase6_eligible=false`, `rights_verified=false`, and release status `BLOCKED_ADAPTER_ONLY`. It reports predeclared CER/coverage facts but makes no release decision. Synthetic tests cover threshold validation, low/high/exact-boundary outcomes, missing/ambiguous groups, and plan mutation/replay. A real run remains blocked because there is no authorized three-group dataset or trusted timed-reference set.

## Why full Phase 5 remains blocked

Crop-level CER/WER and the fixed-SRT adapter measure different surfaces. The adapter can calculate metrics only after preparation rights/provenance gates pass and human-attested time-aligned references exist. It cannot independently verify lawful use, reference trust, or model/device execution, and its report cannot unlock Phase 6. Trusted data, independent rights validation, actual candidate execution provenance, and release-grade evidence remain outstanding. The explicit per-group coverage threshold is now part of a hash-bound pre-result plan; it is a measurement gate, not proof of quality or release authorization. The preparation report stays `PREPARED_NOT_RUN`; the crop report remains crop-only, and the video adapter report remains measurement-only. See the next section for the separate local runner. No model trainer or training flow exists.

## Fixed-profile local OCR runner

`phase5_execution.py` is a separate, opt-in local runner. Before creating output, it validates a frozen Phase 5 execution plan, Phase 2 parent lineage, per-group media hashes, both local offline profiles, a separately reviewed rights receipt, and its project trust pin. It invokes only the fixed product CLI with the selected hash-pinned profile and fixed OCR arguments. The runner records SRT/stats hashes, profile/model/config/device/backend identity, and the run's input bindings. Its test executor is synthetic; it is not a model trainer and does not update OCR weights.

Runner output includes `phase5-execution-receipt-v2.json` and a `prediction-manifest-v1.json` whose `execution_receipt` object binds the receipt's relative path, raw SHA-256, and run ID. The receipt repeats the canonical prediction-record digest and records. Before measurement, the adapter checks this cross-file binding against the prepared dataset/split/reference, media hashes, profile/config identity, SRT/stats files, and cue counts. It labels the result `runner_recorded_not_independently_verified`; legacy manual v1 manifests remain `user_supplied_not_independently_verified`.

This binding is a local consistency check, not a digital signature or independent execution proof. It has no cryptographic nonce: the run ID is a local correlation value, and someone able to rewrite the receipt and manifest can create a new internally consistent pair. It does not establish node-level GPU execution, lawful use, reviewer identity, or trusted evidence; it never changes `rights_verified:false` or `phase6_eligible:false`.

The runner is implemented, but a real run is currently blocked: there is no rights-reviewed authorized corpus or trusted time-aligned reference set, and the Phase 5 execution receipt registry and Phase 6 trust registries are empty. Synthetic fake-executor tests validate local mechanics only; no real video, OCR, or model experiment has run. Do not treat the measurement adapter as the runner or its report as a release PASS.

## Metrics still needed for full-video evaluation

- **CER:** Levenshtein character edits divided by reference characters; apply NFC and whitespace normalization, preserving case and punctuation. Exclude empty-reference examples from CER calculation and report them separately.
- **WER:** after the same normalization, tokenize on Unicode whitespace and divide word edits by reference word count. State the limitation if no language-specific tokenizer is used.
- **Exact match:** matched cues with identical normalized text / reference cues; an unmatched reference cue counts as incorrect.
- **Cue recall:** matched trusted reference cues with a frozen temporal matching algorithm/tolerance / total reference cues.
- **Cue precision:** matched OCR cues / total OCR cues. If these definitions differ from the current overlap-threshold system in `vtt-qa.py`, the adapter must report the difference explicitly.
- **Timing:** separate mean absolute start and end error for matched cues, and—only where reference alignment supports it—the share of pairs within a predeclared tolerance. Freeze the tolerance before the experiment.
- **Coverage:** the verifiable, aligned, measurable share of reference cues; separately count unavailable/ambiguous/missing references and reasons.
- **Aggregates:** results per episode/source; cue-weighted micro (total edits / total reference units) and group macro (simple mean of group scores). Report train, validation, and test separately; the release gate uses only the frozen test group.

Every real measurement must record the same reference, cue alignment, normalization, OCR engine/config, configured-device/session-configured-backend, and run parameters for baseline and candidate. Provider/device values describe run configuration, not a node-level inference profile. Report any differences explicitly. Do not add translation metrics to this OCR report; they require a separate bilingual-human set and schema.

## Predeclared regression gate

Freeze `--max-cer-regression` and the required `--min-test-group-coverage` before running the report. The coverage/CER comparison fails if the candidate exceeds the permitted increase over baseline CER on the frozen test set, or if either baseline or candidate falls below minimum coverage in any test group. Report every episode/source group separately; high coverage in one group cannot hide low coverage in another. Do not change a threshold after seeing results; a new value requires a new experiment plan. These comparisons are still not a Phase 6 release PASS.

## Hard stop conditions

- If the human `accepted`/`corrected` export cannot be validated, or crop hash/decode, cue ID, or schema is invalid: reject preparation.
- If source/episode group information or a license evidence file with a matching hash is missing: do not create a split. Evidence existence is not legal compliance; do not run an experiment before human rights review is complete.
- If the parent review-pack chain cannot be validated, record provenance as `self_attested` and keep the experiment gate closed.
- If fewer than three independent groups exist: do not create train/validation/test; stop evaluation as insufficient data.
- If a source review pack is labeled as multiple source+episode groups or two packs claim the same group: reject metadata lineage.
- Without trusted, time-aligned source-language references: do not report OCR accuracy, model gain, or regression pass.
- If baseline/candidate engine, config, configured-device/session-configured-backend, metric normalization/alignment, or predeclared gate is missing: reject result generation.
- This delivery did **not** train a model, change model weights, or process user video/corpus data. These remain blocked until sufficient data, licensing, and engine-specific references are available.

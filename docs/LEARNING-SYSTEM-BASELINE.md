# Learning system Phase 0: source-backed baseline

**Purpose:** record what the repository can currently observe and measure before adding learning behavior. This is a source and documentation inventory, not a fresh OCR benchmark. No user video, subtitle, crop, or translation corpus is included here.

**Evidence boundary:** the inventory below is based on the named source files, functions, and documentation sections. The current BLEND-S regression result and threshold are previously documented values. They were not rerun while preparing this baseline. The video and reference VTT are external local inputs and are not present in this repository tree.

## Current data path and trust boundaries

| Stage | Current data and behavior | Trust status | Destination / separation |
|---|---|---|---|
| Video sampling and OCR | `hardsub2srt.py` scans subtitle bands locally. The OCR path keeps segments as start/end frame indices, recognized text, and engine confidence before producing timed SRT cues. Product device selection uses CUDA-capable PyTorch when available; CPU is the fallback and `--cpu` explicitly requests it. | OCR text and confidence are observations, not ground truth. Confidence is not a calibrated probability. | The source video is read locally. No video upload or remote OCR service is part of the path. |
| Main SRT and run metadata | `hardsub2srt.py` writes final SRT and run/statistics metadata. The final cue text may include deterministic post-fixes. Review-pack metadata records available tool/device/configuration details. | Final text is still an OCR result until visually verified. | Written to the user's selected local output. Existing runs can contain local source metadata; do not treat these files as shareable by default. |
| Diagnostic QA images | The CLI `--qa` path emits a selected set of diagnostic images. | Useful for inspection, but not a persistent one-image-per-cue label set. | Local QA output; not automatically a training dataset. |
| Review-pack crop mapping | `hardsub2srt.py`'s `build_review_pack` links a final cue to a source OCR segment only when its frame interval maps uniquely and exactly. It reads a representative midpoint frame, crops the relevant subtitle band, and records frame index/time, region, confidence, and mapping status where available. Merged or ambiguous cues remain unavailable. | A mapped crop is evidence for review, not a verified label. Text may be post-fixed. Frame time is estimated from frame index / reported FPS; source presentation timestamps are not preserved, so VFR timing may differ. | Opt-in local `<name>.review-pack/` containing copied final SRT, manifest, and crops. The source video path and video hash are omitted. SRT text and crops remain sensitive content. |
| Human OCR review | The local `/ocr-inceleme` page records append-only decisions in `review-events.jsonl`. Accepted or corrected source text is tied to the pack and crop hashes. Dataset export snapshots only active, valid, human-accepted/corrected crop-text pairs into a separate `verified-ocr-dataset-.../` directory. Uncertain, undone, stale, invalid, unavailable, or mismatched items are excluded. | Human visual confirmation is the authority for OCR labels. AI suggestions are not labels. | Local review pack and explicit local dataset export. No OCR weights are updated by this action. |
| Manual AI translation review | The Phase 3 UI exports a selected request for cues with accepted/corrected OCR review and valid crops. A user can send that JSON to an AI outside the app and import its structured response. The app itself makes no provider call. | Imported responses are proposals. Only explicit user-accepted/edited decisions can become approved translation memory; the source review must be valid and confirmed. | Request file is user-controlled and contains selected crops/text. Local sidecar, proposal/decision events, and rebuildable memory snapshot remain in the review pack. No automatic provider upload or VDS transfer. |
| Existing text correction tools | `/api/ogret` writes whitespace-normalized `old<TAB>new` pairs to `kullanici-sozlugu.txt`; `_kullanici_uygula` applies literal, case-sensitive substring replacements. The SRT editor's `ogrenme_yurut` invokes `ogren.py` for changed text and writes a per-SRT report. | These are text correction records, not visually verified OCR labels and not approved translation memory. A broad replacement can affect every matching substring. | Local text dictionary and per-SRT report. They remain separate from crop review and translation review. |

Source references: `hardsub2srt.py` functions `scan_band`, `_kullanici_sozluk_yukle`, and `_kullanici_uygula`, plus the `build_review_pack` call site; `review_bundle.py` function `build_review_pack`; `ui_server.py` routes `/api/ogret` and `/ocr-inceleme`, plus `ogrenme_yurut`; `ogren.py`; `docs/REVIEW-BUNDLE.md` sections “Manifest fields”, “Human OCR verification (Phase 2)”, and “Manual AI translation review (Phase 3)”; `docs/LEARNING-SYSTEM-PLAN.md` section “Current behavior verified in this repository”.

## Existing OCR regression baseline

`regresyon/gt_cases.json` currently defines one trusted-reference case:

| Case | Window and configuration | Previously documented baseline | Predeclared gate | Scope |
|---|---|---|---|---|
| `blend-s-ep01-240` | BLEND-S S01E01, first 240 seconds; fixed band `(820, 260)`, `thr` mask, white threshold `240`; product device selection unless `--cpu` is requested | Corpus CER `3 / 201 = 0.0149253731`; 6 aligned cue pairs | Corpus CER ≤ `0.035`; at least 6 aligned pairs, 201 GT characters, and 8 reference cues | One 240-second excerpt only; not a claim about arbitrary videos, styles, or languages. |

The `video_glob` and `_gt2-blends.vtt` reference are supplied through `gt_gate.py`'s external `--video-root` and `--reference-root` arguments. They are not present in the repository. `regresyon/README-kisa.md` documents that absent or ambiguous assets are unavailable, not passing. It also documents exclusions: 86tr's available reference is time-incompatible (reported CER 1.0311 with a timing warning), and Wano lacks a trusted time-compatible reference. These cases must not be silently folded into the supported baseline.

The separate fixed-frame comparison in `regresyon/regresyon.py` uses `regresyon/baseline.json` and `regresyon/noktalar.json`. `regresyon/README-kisa.md` documents point-level normalized text CER and average/worst-point thresholds. Its frame assets are kept local for copyright reasons, so it is a separate OCR-engine regression check rather than a video-to-SRT end-to-end ground-truth set.

## Metric coverage

| Planned measure | Source-backed current support | Limitation / status |
|---|---|---|
| Corpus CER | `vtt-qa.py` `analyze` calculates edit distance divided by total aligned reference characters; `gt_gate.py` reports current CER and delta from the configured baseline. | Available for configured, sufficiently aligned reference cases only. Current video gate has one case. |
| Mean per-cue CER | `vtt-qa.py` reports mean CER over aligned non-empty reference cues. | Not the same as an episode/source macro average; this is not an aggregate over a representative corpus. |
| Cue alignment counts and matched-based precision/recall | `vtt-qa.py` reports aligned, unmatched SRT/reference cues, and match-based precision/recall. | These are alignment-derived proxies. They are not a separately validated cue-detection benchmark with independently adjudicated cue boundaries. `gt_gate.py` currently serializes recall but not precision. |
| Start timing | `vtt-qa.py` reports mean signed and absolute start-time differences and a first-five-cue compatibility warning. `gt_gate.py` includes the timing object and fails on the compatibility warning. | End-time error and percent within a declared timing tolerance are not currently measured/reported by the GT gate. |
| Exact-match cue rate | Not separately reported. | Could be defined from aligned cue pairs, but must define normalization and denominator before reporting. |
| WER | Not separately reported by the documented GT gate. | Requires an explicit tokenization/normalization contract, especially for Turkish punctuation and apostrophes. |
| Macro/micro across held-out episodes or sources | Not available across a corpus. `gt_gate.py` reports a per-case result; the configured end-to-end set is one excerpt. | Requires multiple trusted aligned cases and a fixed aggregation rule. |
| OCR confidence/low-confidence coverage | Run metadata and review manifest preserve confidence/device information where available; UI stats can report low-confidence counts/rates. | Confidence is not calibrated; counts are diagnostics, not accuracy scores. |
| Translation quality | No measured translation corpus, human rubric results, or acceptance/edit/rejection report exists. Phase 3 records decisions but does not calculate quality metrics. | Requires verified source text, bilingual human evaluation, a held-out set, and a consistent rubric. LLM self-scores alone are insufficient. |

Source references: `vtt-qa.py` function `analyze`; `regresyon/gt_gate.py` function `analyze_case`; `regresyon/gt_cases.json`; `regresyon/README-kisa.md` sections “VTT referanslı kapı”, “Sabit kare baseline kıyası”, and “Şu an kapsanmayan vakalar”; `docs/LEARNING-SYSTEM-PLAN.md` sections “OCR loop metrics” and “Translation loop metrics”.

## Translation evaluation contract (not yet measured)

Evaluate only cues whose source text has a valid human OCR review. A bilingual human rubric should separately rate:

- semantic adequacy, including changes in meaning;
- omissions and additions;
- terminology consistency;
- requested style and register.

Record human accept, edit, and reject outcomes and issue categories. Keep OCR measurements and translation measurements on separately defined populations, so an OCR change cannot appear to improve translation merely by changing which cues are evaluated. Do not report an aggregate score until the rubric, scale, adjudication rules, language pair, corpus, and denominator have been fixed. No translation score is available from this repository's current test/reference data.

## What is needed for reliable broader claims

The current repository artifacts are enough to document the pipeline, schema boundaries, one predeclared OCR gate, and metric definitions. They are not enough to freshly run or generalize that gate in this worktree. To establish a broader baseline or support a 95–99% target, the project needs:

1. A user-authorized, legally usable corpus with local videos and trusted source-language subtitles aligned to those videos, across the intended supported episode/source conditions.
2. A frozen case manifest naming language, video conditions, time window, OCR configuration, device/backend, normalization, alignment algorithm, and thresholds **before** candidate results are examined.
3. Episode/source-level holdout assignments so cues from one episode do not leak across tuning and evaluation.
4. A declared definition of the target metric. “Accuracy” must name its denominator and scope (for example, normalized exact-match cue rate or corpus CER on held-out cases); it cannot imply a guarantee for arbitrary uploads.
5. For translation, a separate held-out bilingual-reviewed set with the rubric above and explicit accepted/edit/rejected outcome counts.

The existing BLEND-S threshold is retained as a regression gate for that single documented excerpt. Do not reinterpret it as a universal 96.5% accuracy guarantee. If trusted aligned references or defensible cue-to-frame provenance are unavailable, record the metric as unavailable and do not claim measured gain or create OCR training labels.

## Phase 0 completion criteria

- The inventory above can be traced to the named implementation and documentation sources.
- Existing baselines, scope, exclusions, unavailable assets, and metric limitations are stated without claiming a fresh run.
- OCR and translation metrics have separate populations and definitions; missing measures are identified rather than inferred.
- No user video, subtitle text, crop, absolute user path, or externally submitted provider data is included.
- No new data sharing, provider call, or VDS service is needed for this inventory. New accuracy claims remain blocked until authorized trusted evaluation data is available.

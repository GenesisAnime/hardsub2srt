# Assistant-reviewed OCR seed import

`ocr_seed_import.py` validates and prepares an existing
`training-seed.jsonl` for another human review in the local `/ocr-inceleme`
page. Current seed rows are `assistant_visual_review` candidates with
`user_confirmation_status: pending`, `rights_status: not_verified`,
`dataset_status: candidate_needs_human_review`, and
`training_eligible: false`. The importer rejects rows that do not have these
pending-candidate values. It does not create review events, accept the proposed
text, mark training eligibility, or train/update an OCR model.

A no-output validation prints the input snapshot's SHA-256 and summary counts.
Those values identify a particular local dataset, so this guide does not publish
a real seed fingerprint, row count, episode list, or pack count.

Run a no-output validation first:

```powershell
$seed = 'C:\path\to\training-seed.jsonl'
py -3 ocr_seed_import.py $seed --check
```

`--check` reads the seed and image evidence, checks hashes and image decoding,
and reports counts/SHA-256. It writes no review packs or report files.

```powershell
$seed = 'C:\path\to\training-seed.jsonl'
$out = Join-Path $env:LOCALAPPDATA 'hardsub2srt\seed-import-example'
py -3 ocr_seed_import.py $seed --out $out
```

The output path must not already exist. Keep it outside OneDrive or another
synchronized folder. The importer copies the JSONL and referenced original
evidence into the output; it never edits the seed, evidence, corrected SRT
copies, or original benchmark. It then creates one `.review-pack` per
`episode_key + video fingerprint`. The pack's SRT is a synthetic subset made
only from that seed group's observed OCR strings and cue times; it is not the
original full-run SRT. The `/ocr-inceleme` start page lists the verified local
seed packs by episode; **Bölümü incele** opens a selected pack. The image is
normalized to a bounded JPEG for the review UI; the original image bytes and
SHA-256 remain beside the packs, and each manifest records both the original
image hash and the normalized crop hash.

The old OCR text is the pack's SRT text. The seed's `target_text` appears in
the page as **“Seed önerisi · insan kararı değil”**. **“Öneriyi düzenleme alanına
aktar”** only copies it into the text field. The user must still inspect the
image and press **“Görüntü doğru”**, **“Düzeltmeyi kaydet”**, or **“Belirsiz”**.
Only those normal UI actions append a review event. Import reports zero
human decisions and zero export labels. Export includes only later active
human `accepted` or `corrected` events. `uncertain` is excluded. There is no
separate `rejected` event in the current schema.

Rows marked `remove_cue` / `training_eligible: false` are listed in
`import-report.json` as skipped and are not put into the candidate review packs
or OCR text export. A no-subtitle image is not a transcript label; using it for
detector training would require a separate negative-example schema. Human
review does not automatically change the seed's `training_eligible:false` or
unverified rights fields.

`episode_key` and the video fingerprint are preserved in the review-pack
manifest and import report, but the current Phase 2 export rows and blank Phase
5 metadata template do not infer or carry `source_id`, `episode_id`, or rights
approval. Fill those fields from independently checked source records after
review. The importer does not assert ownership or training permission. Preserve
the output packs, `review-events.jsonl`, evidence copy, and seed together; the
existing lineage verifier needs the parent pack when validating an export.

The historical quality editor may also open a seed crop as evidence, but only
when the historical stats video fingerprint, cue start/end (within 800 ms), and
complete old OCR text match exactly one seed cue. It serves the crop only from
the validated local seed review-pack root; no match or multiple matches leave
the **Görsel yok** state. This evidence lookup does not alter SRT text or create
a review decision. This older historical viewer has no local-video picker or
seek control; it does not open the video itself.

At least three distinct verified packs are needed by the current composite
dataset assembler. This produces reviewable Phase 2 examples only. Existing
dataset preparation/evaluation tools do not train a model.

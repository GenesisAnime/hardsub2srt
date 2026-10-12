# Local bilingual translation quality evaluation (Phase 3)

## What this adds

The local `/ocr-inceleme` page now has a bilingual human scoring flow separate from OCR correction, translation accept/edit/reject decisions, and translation memory. AI output still comes from a manual conversation/file round-trip: import the structured response in the existing translation panel, then score the imported proposal with the frozen rubric. The app makes no provider/API call and does not send data anywhere.

The rubric rates semantic adequacy, omissions, additions, terminology, and style/register on a frozen 0–2 scale. The local blind card shows the image crop, verified source cue plus immediately adjacent cues only when those neighbors also have active human OCR decisions, the AI proposal, target style, and glossary. Reviewers can mark a candidate unevaluable and choose a reason. Events append to a separate local quality log; they never write to the OCR event log, translation decision log, or translation memory. The UI can correct an existing rating by appending a new event that names the prior event it supersedes; prior history remains intact.

## Two separate modes

- **Synthetic demo:** available without a real corpus. It uses fixed fake source/context/candidate strings and no video, crops, SRT, model, or provider. Its report suppresses all quality distributions and rates and always says Phase 6 is ineligible. Synthetic results can test page/API mechanics only.
- **Human held-out evaluation:** remains blocked until the server validates the registered Phase 5 evidence and current manual AI proposals. If evidence becomes valid, the UI can open it without a code change. Ratings are tied to the frozen blind candidate, source review event, source/crop/SRT/proposal hashes, and current verified evidence. Any stale evidence blocks reads and writes.

The browser cannot submit source state, cohort split, human-held-out status, rights status, timed-reference status, provider/system condition mapping, or source/crop/SRT/proposal hashes. Those are derived by re-reading the local registry and existing Phase 2/5 artifact validators on the server. Blind IDs are opaque; candidate-to-source links stay in a private local sidecar, and provider/system labels stay in the separate owner-local condition map, outside the blind UI and aggregate report.

## Registering evidence for future real scoring

The server looks for `real-cohort-registry-v1.json` inside the local evaluation root. On Windows that is normally `%LOCALAPPDATA%\hardsub2srt\translation-evaluation-v1\real-cohort-registry-v1.json`; on Linux it is under `$XDG_STATE_HOME/hardsub2srt/translation-evaluation-v1/` or `~/.local/state/hardsub2srt/translation-evaluation-v1/`. The file is local configuration, not an API request. The application rejects synced/network evidence paths and symlink/junction components and stores evaluation events under the `real/` child of that root. POSIX permissions are restricted to the current user; on Windows the directory inherits the OS local-app-data ACL (the application does not claim to set a custom ACL).

Example schema (replace paths with actual local artifacts; this example is not evidence and does not enable scoring by itself):

```json
{
  "schema_version": 1,
  "dataset_path": "C:\\Users\\example\\AppData\\Local\\hardsub2srt\\verified-ocr-dataset-001",
  "metadata_path": "C:\\Users\\example\\AppData\\Local\\hardsub2srt\\phase5\\metadata.json",
  "experiment_path": "C:\\Users\\example\\AppData\\Local\\hardsub2srt\\phase5\\experiment-report.json",
  "timed_reference_path": "C:\\Users\\example\\AppData\\Local\\hardsub2srt\\phase5\\timed-references.json",
  "review_pack_paths": [
    "C:\\Users\\example\\AppData\\Local\\hardsub2srt\\episode-a.review-pack",
    "C:\\Users\\example\\AppData\\Local\\hardsub2srt\\episode-b.review-pack",
    "C:\\Users\\example\\AppData\\Local\\hardsub2srt\\episode-c.review-pack"
  ],
  "source_language": "ja",
  "target_language": "tr",
  "style_profile": "Describe the target style before reviewing.",
  "glossary": "Describe required terms before reviewing.",
  "minimum_rated_fraction_per_held_out_group": 1.0
}
```

The owner also maintains a separate `condition-mapping-v1.json` beside the registry. It is read only by the local server and is never returned in blind status or aggregate reports. Assign each held-out proposal to an opaque condition ID; all selected proposals must resolve to exactly one ID or cohort creation is rejected. Example shape:

```json
{
  "schema_version": 1,
  "conditions": [
    {"condition_id": "0123456789abcdef0123456789abcdef", "private_label": "private AI/provider setup label"}
  ],
  "proposal_assignments": {
    "abcdef0123456789abcdef0123456789": "0123456789abcdef0123456789abcdef"
  }
}
```

The private label is revealed through the local UI only after every blind candidate has an active rating. Mixed condition IDs are rejected before scoring; a hidden label alone is not used to imply blindness.

The server revalidates dataset and metadata hashes, Phase 2 OCR event/crop provenance, completed human rights review and hash-linked evidence, the frozen source/episode split, trusted timed references, matching source/target languages, one current manual AI proposal for every test cue, and its private condition assignment. At least three source/episode groups and a non-empty test split are required. The predeclared per-group rating floor is mandatory and has no implicit default. The cohort must map all selected proposals to one opaque condition ID; mixed condition assignments are rejected. The program cannot independently identify which AI/provider produced manually imported text, so even a one-condition assignment is user-maintained provenance and rates remain descriptive rather than system-comparative. The current repository has no eligible real bilingual held-out cohort; do not populate these files with synthetic or unverified artifacts to get past the blocker.

This rights check confirms the repository's recorded human review and evidence hashes. It does not independently establish legal rights or legal advice. Only use data you are authorized to process and share with the provider you chose for the manual round-trip.

## Interpretation and limits

The aggregate rate is a descriptive held-out “no material defect” rubric rate for the exact registered population, not a perfect-translation rate, guarantee for arbitrary episodes, or 95–99% product accuracy claim. The denominator and excluded items are explicit; excluded/unratable items remain failures in the denominator. The report also shows per-group rating coverage and the frozen floor. If a group misses the floor, the rate is suppressed. Proposal-to-condition assignment is maintained in the owner-only local mapping; mixed assignments fail closed, but the app cannot independently attest provider identity. Therefore the rate remains descriptive-only. `phase6_eligible` remains false in this evaluator.

Reports contain hashes and aggregate counts only, never cue text, crop images, provider names, or absolute paths. Source text and candidate text are shown only in the local review screen. The local evaluation files can contain sensitive source/translation judgments, so keep the app-data directory private and non-synced. Current synthetic tests prove mechanics and tamper rejection only; they do not measure translation quality.

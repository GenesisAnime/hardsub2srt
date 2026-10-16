# OCR correction safety audit (draft)

This draft documents two reproducible risks in the current `hardsub2srt.py` correction path. **It does not claim that either risk has been fixed.** The repository version may be behind the local working copy.

## 1. Unbounded substring replacement

`_kullanici_uygula` uses `metin.count(eski)` and `metin.replace(eski, yeni)` across the entire cue. A learned correction for a short token can silently rewrite parts of otherwise correct words. For example, a rule `can -> Can` also changes `candan` to `Candan`. It is especially dangerous for multilingual OCR and proper nouns.

**Suggested fix:** introduce explicit match scopes (`exact_cue`, `exact_line`, `whole_word`) and require opt-in for legacy substring matching. Preserve existing dictionaries through migration and show previews before enabling migrated rules. Add Turkish Unicode boundary tests and record rule provenance.

## 2. Rules are applied twice in the post-fix chain

`_duzelt_blok_metni` invokes `_kullanici_uygula` both before and after `_duzelt_metin`. This is intentional for raw/post-fix matching, but creates an unguarded possibility of applying one rule twice or cascading through other rules. For example, `a -> aa` applied twice expands one character to four even without any OCR improvement.

**Suggested fix:** track rule IDs and their application locations within a cue; prohibit accidental reapplication unless the rule explicitly targets a different stage. Keep a structured audit log with original text, proposed text, stage, rule ID, and reviewer decision.

## Required regression coverage

- Substrings inside correct words are not modified by whole-word rules.
- Turkish casing and punctuation boundaries are respected.
- Rules do not silently fire twice on the same span.
- Rules from the raw OCR stage and the post-fix stage remain distinguishable.
- Existing legacy dictionaries produce a migration preview, not silent behavior changes.
- Visual verification is required before corrections enter verified OCR training labels.

## Scope and validation

These risks were identified by static inspection of `_kullanici_uygula` and `_duzelt_blok_metni`. No local video, reference subtitle corpus, or runtime benchmark was available; no accuracy improvement is claimed. The proposed behavioral change needs compatibility tests and real review-pack examples before merging.

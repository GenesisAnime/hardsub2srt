# hardsub2srt

> Extracts embedded (hardsub) subtitles from video into Turkish `.srt` files —
> two-engine OCR, a Turkish correction layer, a self-improving learning loop,
> and quality-assurance tooling.

[Türkçe](README.md) · **English**

---

## What it does?

Takes a video with burned-in subtitles, finds the subtitle band automatically,
runs OCR frame by frame and produces a timed Turkish `.srt`. No special markers
are written inside the SRT (that would break players); the matching info goes
into a sidecar file instead.

| Layer | What it does |
|---|---|
| **Band detection** | Pixel-measured automatic subtitle band; widens narrow bands, protects against white-background footage |
| **Two-engine OCR** | Main engine (EasyOCR) + PaddleOCR (PP-OCRv6) as a second opinion: votes and swaps on low-confidence blocks |
| **Correction** | Turkish diacritics, dropped letters, apostrophe table, user dictionary (`kullanici-sozlugu.txt`) |
| **Learning** | `ogren.py` + "Save = learn" in the UI — every fix improves future runs |
| **Quality assurance** | QA contact sheets, noise separation (`_ekran.srt`), block statistics, video-hash sidecar (`.hardsub2srt.json`) |
| **Measurement** | `vtt-qa.py` ground-truth comparison + a 12-point regression set (`regresyon/`) |

## Quick start

```sh
py -3 hardsub2srt.py "episode.mp4"       # produces episode.srt
py -3 hardsub2srt.py "episode.mp4" --cpu # on machines without a GPU
py -3 ui_server.py                       # web UI on http://127.0.0.1:8765
```

Requirements: Python 3.10+, the OCR stack from `requirements.txt`
(EasyOCR, RapidOCR, onnxruntime, OpenCV, Flask), ffmpeg on PATH. GPU optional.

Each run produces: `episode.srt` + `episode.stats.json` (block statistics) +
`episode.hardsub2srt.json` (video hash + parameters — so an SRT can always be
tied to the exact video it came from).

## Measurement

Accuracy claims are measured against ground truth, not guessed:

| Test | Result |
|---|---|
| E02 ground-truth comparison (vtt-qa) | CER 0.48%, recall 100% |
| BLEND-S (stylized font) | CER 1.49% |
| Regression set (12 points) | `py -3 regresyon.py --karsilastir <new.json>` — exits 1 if degradation thresholds are crossed |

## Community learning loop (under construction)

The idea: user corrections flow back without collecting any subtitle text.
The program produces an anonymous "learning package", the user attaches it to
a GitHub Issue, packages get merged by vote and ship with the next release.
How it works: [TOPLULUK-OGRENME-TASARIMI.md](TOPLULUK-OGRENME-TASARIMI.md).

**No subtitle text or anime frames are uploaded to this repo**; what gets
shared is statistics, word-level dictionary entries and measurement data.
Rules in [CONTRIBUTING.md](CONTRIBUTING.md).

## File map

| File | What |
|---|---|
| `hardsub2srt.py` | Extraction engine (CLI) |
| `ui_server.py` | Flask UI: runs, corrections, learning, quality panel |
| `ogren.py` | Automatic learning CLI (safe dual classifier) |
| `vtt-qa.py` | Ground-truth/VTT measurement |
| `toplu.bat` | Batch runs + done-list (skips already-processed, `--yeni` bypasses) |
| `regresyon/` | 12-point OCR regression set + comparison script |
| `docs/` | Development log, decision records, lessons |
| `kullanici-sozlugu.txt` | Word-level correction dictionary (grows with the community) |

## Documentation

If you wonder how the project got here and why it is built this way:

- [Development log](docs/GELISTIRME-GUNLUGU.md) — when, why and with what
  measurement each step was taken
- [Decision records](docs/NASIL-VE-NEDEN.md) — the reasoning behind 10 key
  decisions, from the copyright wall to correction rules
- [Lessons](docs/OGRENMELER.md) — traps caught during development and what
  they taught us

## Roadmap

1. Learning-package export + `birlestir.py` (vote-based merging)
2. Low-confidence block rescue pass (upscale/contrast second attempt)
3. Font-profile mining: best engine/parameter combination per profile
4. Single-file `.exe` distribution (CPU by default, GPU optional)

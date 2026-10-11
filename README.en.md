# hardsub2srt

hardsub2srt converts burned-in subtitles in video into timed Turkish `.srt` files using OCR. Video decoding and OCR run locally on the user's Windows/Python machine.

**English** · [Türkçe](README.md)

## Quick start (Windows)

1. Install Python 3.10+ and FFmpeg; make sure both `ffmpeg` and `ffprobe` are on `PATH`.
2. From the repository directory, install Python dependencies:

   ```powershell
   py -3 -m pip install -r requirements.txt
   ```

   EasyOCR/PyTorch may download model files on first use. GPU use depends on a compatible PyTorch/CUDA installation; you may need to install the CUDA-enabled PyTorch build appropriate for your machine and driver separately. This repository does not currently provide a packaged `.exe` or a locked/reproducible environment.

3. Process one video:

   ```powershell
   py -3 hardsub2srt.py "D:\Videos\episode.mp4" -o "D:\Subtitles\episode.srt"
   ```

   OCR selects the GPU when CUDA-enabled Torch is available and otherwise falls back to CPU. Add `--cpu` to explicitly force CPU:

   ```powershell
   py -3 hardsub2srt.py "D:\Videos\episode.mp4" -o "D:\Subtitles\episode.srt" --cpu
   ```

   `cikar.bat` supports drag and drop; `toplu.bat` is the Windows batch launcher. The CLI output path is supplied with `-o`.

## Local web UI

Run `arayuz.bat` or `py -3 ui_server.py`. The UI is served by a local Flask process at `http://127.0.0.1:8765`, bound to this computer. The browser is the interface; the local server starts the Python OCR subprocess.

The UI can select multiple video files or scan a folder. On Windows, native dialogs pass local paths to the local server; video contents are not uploaded through the browser. The native picker is Windows-specific; enter paths manually on other systems.

A single worker processes jobs sequentially. Each `/api/ekle` request accepts at most 100 videos; the UI sends larger selections in chunks of 100. The total waiting-plus-active limit is 2,000 jobs. Status responses show the most recent 100 records and pin the active job separately. Duplicate paths already waiting or running are skipped. **Queued jobs and the detailed live log are in memory:** pending jobs are not restored after a restart. A bounded diagnostic summary of recent runs is stored at `%LOCALAPPDATA%\hardsub2srt\job-history.json` on Windows or `$XDG_STATE_HOME/hardsub2srt/job-history.json` on Linux/macOS (when XDG is unset, `~/.local/state/hardsub2srt/job-history.json`). `GET /api/is-gecmisi` returns the latest 20 records by default (`?limit=1..50`). History is limited to 100 records, 30 days, and 64 KiB; it contains no video/output paths or names, SRT text, or images. It does not restore queue jobs.

Each UI job gets a unique folder beneath the selected output directory: `runs/video-<name>/<timestamp>_<job-id>/`. New jobs do not overwrite earlier run output. The run may contain the SRT, stats/run JSON sidecars, requested `qa/` images, a VTT comparison report, or an ASS file. CLI output instead follows the explicit `-o` path.

## Outputs and quality checks

A CLI run such as `-o ...\episode.srt` writes the SRT and at least a `.stats.json` file. A `.hardsub2srt.json` sidecar records tool/version and video-match parameters. Sidecars are not video files, but may contain local video name, size/mtime, and a partial hash; review them before sharing.

When a review pack is requested, it includes raw writer-end diagnostics only if the generation-time `main_srt_sha256` in stats matches the exact main SRT bytes. Matching clamped cues may include `raw_end_ms`, `clamp_delta_ms`, and an explicit approximate frame/FPS/no-source-PTS caveat. Missing or stale stats omit these fields and state the reason in `raw_timing_metadata`. AI request timing stays tied to the emitted SRT values, and responses cannot retime cues.

The OCR engine extracts visible subtitles; it does not translate them by itself. Optionally, the local UI's `/ocr-inceleme` page can prepare a chat package for selected, visually verified cues. You may send that package to an AI service yourself and import its response. The app does not call GPT/DeepSeek APIs or send these files over the network; sharing with an external AI is a user action. Explicitly approved translations can also be saved to a local project-and-language-scoped memory, and terminology suggestions can be applied to a draft by the user. See [local review and learning workflow](docs/REVIEW-BUNDLE.md). `vtt-qa.py` and `regresyon/gt_gate.py` compare OCR text with time-aligned source-language references; they do not score translation quality. `gt_gate.py` requires externally supplied video and trusted VTT assets; missing or ambiguous inputs are not counted as a pass. See [regression usage](regresyon/README-kisa.md). Videos, VTTs, and anime frames should not be added to the repository.

### AI OCR visual review and translation review

These are separate manual AI workflows. **AI OCR visual review** is available for OCR cues with a valid crop before a human has approved the text. The user sends the OCR text and crop image to an AI; the imported response is stored only as a proposal. An AI proposal never becomes an OCR label, training example, correction-dictionary entry, or translation-memory item by itself. The user must accept or correct the source text through the normal visual review controls before it can be a human OCR label.

**AI translation review** is gated on a human-accepted or human-corrected source text and a valid crop. The AI response remains a proposal; the user must explicitly accept or edit it before it can enter translation memory. The app does not call GPT/DeepSeek APIs; the user manually shares the review files with the external AI.

The review ZIP, its sidecar, and the AI proposal log are written inside the selected `.review-pack`; OneDrive may sync them automatically. For strict local-only handling, copy the **entire pack** (including its manifest, SRT, `review-events.jsonl`, and `crops/`) to a non-synced local folder and open that copy for review. See [local review and learning workflow](docs/REVIEW-BUNDLE.md) and [AI OCR visual review](docs/AI-OCR-GORSEL-INCELEME.md).

## Dependencies and troubleshooting

`requirements.txt` lists Python packages: NumPy, OpenCV, EasyOCR, RapidOCR, ONNX Runtime, and Flask. FFmpeg/ffprobe are separate system dependencies. Check PyTorch/CUDA with `py -3 -m pip show torch` and `py -3 -c "import torch; print(torch.cuda.is_available())"`. If the UI or CLI cannot find `ffmpeg`/`ffprobe`, add FFmpeg to `PATH` and open a new terminal. Use `--cpu` when GPU support is unavailable or incompatible; processing may be considerably slower.

- If the UI does not open, check `arayuz.bat` output, Flask installation, and whether another process occupies port 8765.
- Watch queue counts and per-job errors. Inspect the job log and run folder for a failed job. Restarting the server does not restore pending jobs.
- Model downloads and GPU/PyTorch package sizes vary by internet connection, Python version, and driver.

## Privacy and optional metrics contribution

OCR and AI review remain local. There are no GPT/DeepSeek API calls. An optional contribution API prototype is included, but **it has not been deployed to the VDS** and no remote submission has been made. The local `/katki` page first builds a network-free preview for a completed run; only its separate consent checkbox and **Send** click starts an HTTPS request. Without URL, token, and retention-day configuration, sending is disabled; the displayed retention days must match the API policy. The v1 JSON contains only numeric timing/frame/cue/low-confidence summaries and OCR device/engine classes. Video, images, SRT/VTT/text, filenames/paths, usernames, hashes, and persistent device IDs are not accepted. CER is disabled in the UI; the API contract requires a trusted local VTT and separate CER consent. The API refuses to start until an operator explicitly configures a retention period.

See [the optional contribution API and VDS preparation](docs/CONTRIBUTION-API.md) for deployment steps, token lifecycle, retention/deletion, and TLS proxy boundaries. Live deployment needs a real domain/TLS setup, Windows service account, persistent backed-up storage, firewall rules, and a retention decision. GitHub Pages does not run OCR or host this API. See also:

- [Local client and architecture roadmap](docs/LOCAL-ARCHITECTURE-ROADMAP.md)
- [Optional contribution API and VDS preparation](docs/CONTRIBUTION-API.md)
- [Browser OCR technical plan](docs/BROWSER-OCR-PORT-PLAN.md)
- [OCR and translation learning-system plan](docs/LEARNING-SYSTEM-PLAN.md)
- [Phase 0 source-backed baseline](docs/LEARNING-SYSTEM-BASELINE.md)
- [Local OCR review and learning workflow](docs/REVIEW-BUNDLE.md)
- [Contribution rules](CONTRIBUTING.md)

## Files

| File | Purpose |
|---|---|
| `hardsub2srt.py` | CLI extraction/OCR engine |
| `ui_server.py` | localhost Flask UI, job queue, local subprocesses |
| `job_history.py`, `windows_job.py` | Bounded local job history and Windows child-process ownership |
| `ocr_review.py`, `ai_ocr_review.py`, `review_bundle.py` | Human OCR image review, manual AI visual proposals, and review-pack creation |
| `translation_review.py`, `translation_memory.py` | Source-verified translation proposals and user-approved local translation memory |
| `ocr_dataset_metadata.py`, `ocr_dataset_merge.py` | Dataset rights/provenance metadata and safe pack merging |
| `ocr_experiment.py`, `ocr_crop_eval.py`, `ocr_video_eval.py`, `ocr_video_eval_templates.py`, `phase6_release.py` | Offline OCR experiments, crop/full-video evaluation, and release gate |
| `contribution_client.py`, `contribution_api.py` | Opt-in, consent-gated numeric contribution client and WSGI API |
| `schemas/contribution-metrics-v1.schema.json` | Strict v1 contribution payload contract |
| `srt_format.py` | SRT timestamp formatting helpers |
| `docs/RUN-TIMING.md` | OCR runtime statistics fields and measurement scope |
| `cikar.bat`, `arayuz.bat`, `toplu.bat` | Windows launchers |
| `srt2ass.py` | SRT-to-ASS conversion |
| `vtt-qa.py` | SRT/VTT text and timing comparison |
| `ogren.py`, `kullanici-sozlugu.txt` | Local correction/learning tools and dictionary |
| `regresyon/` | Frame regression and video+GT OCR gate |
| `docs/AI-OCR-GORSEL-INCELEME.md`, `docs/REVIEW-BUNDLE.md` | AI OCR visual proposals and human review workflows |
| `docs/LEARNING-SYSTEM-PLAN.md`, `docs/LEARNING-SYSTEM-BASELINE.md` | Phases and measured current state |
| `docs/OCR-OFFLINE-EXPERIMENT.md`, `docs/FIRST-DATASET-QUICKSTART.md` | Offline evaluation and dataset preparation |
| `docs/LOCAL-ARCHITECTURE-ROADMAP.md`, `docs/` | Local architecture and other development notes |

## License

MIT. OCR models, PyTorch/CUDA, FFmpeg, and other third-party components may have separate licenses and distribution terms; model weights are not included in this repository.

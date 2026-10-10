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

A single worker processes jobs sequentially. Each `/api/ekle` request accepts at most 100 videos; the UI sends larger selections in chunks of 100. The total waiting-plus-active limit is 2,000 jobs. Status responses show the most recent 100 records and pin the active job separately. Duplicate paths already waiting or running are skipped. **The queue is in memory:** pending jobs and session history are not durable across server shutdown/restart.

Each UI job gets a unique folder beneath the selected output directory: `runs/video-<name>/<timestamp>_<job-id>/`. New jobs do not overwrite earlier run output. The run may contain the SRT, stats/run JSON sidecars, requested `qa/` images, a VTT comparison report, or an ASS file. CLI output instead follows the explicit `-o` path.

## Outputs and quality checks

A CLI run such as `-o ...\episode.srt` writes the SRT and at least a `.stats.json` file. A `.hardsub2srt.json` sidecar records tool/version and video-match parameters. Sidecars are not video files, but may contain local video name, size/mtime, and a partial hash; review them before sharing.

This tool transcribes visible text; it does not translate subtitles or evaluate translation quality. `vtt-qa.py` compares output with a time-compatible reference VTT for alignment/CER and timing. `regresyon/gt_gate.py` reruns OCR on externally supplied video and trusted VTT assets; missing or ambiguous inputs are not counted as a pass. See [regression usage](regresyon/README-kisa.md). Videos, VTTs, and anime frames should not be added to the repository.

## Dependencies and troubleshooting

`requirements.txt` lists Python packages: NumPy, OpenCV, EasyOCR, RapidOCR, ONNX Runtime, and Flask. FFmpeg/ffprobe are separate system dependencies. Check PyTorch/CUDA with `py -3 -m pip show torch` and `py -3 -c "import torch; print(torch.cuda.is_available())"`. If the UI or CLI cannot find `ffmpeg`/`ffprobe`, add FFmpeg to `PATH` and open a new terminal. Use `--cpu` when GPU support is unavailable or incompatible; processing may be considerably slower.

- If the UI does not open, check `arayuz.bat` output, Flask installation, and whether another process occupies port 8765.
- Watch queue counts and per-job errors. Inspect the job log and run folder for a failed job. Restarting the server does not restore pending jobs.
- Model downloads and GPU/PyTorch package sizes vary by internet connection, Python version, and driver.

## Privacy and future work

Processing is local by default. Central telemetry, a VDS API, GitHub Pages OCR, and automatic user-file uploads are **not implemented** in this release. Any future measurement-sharing feature should require explicit user choice and a reviewable submission summary; video, raw SRT, full subtitle sentences, and personal paths should not be uploaded by default. See:

- [Local client and architecture roadmap](docs/LOCAL-ARCHITECTURE-ROADMAP.md)
- [Browser OCR technical plan](docs/BROWSER-OCR-PORT-PLAN.md)
- [Contribution rules](CONTRIBUTING.md)

## Files

| File | Purpose |
|---|---|
| `hardsub2srt.py` | CLI extraction/OCR engine |
| `ui_server.py` | localhost Flask UI, job queue, local subprocesses |
| `srt_format.py` | SRT timestamp formatting helpers |
| `cikar.bat`, `arayuz.bat`, `toplu.bat` | Windows launchers |
| `srt2ass.py` | SRT-to-ASS conversion |
| `vtt-qa.py` | SRT/VTT text and timing comparison |
| `ogren.py`, `kullanici-sozlugu.txt` | Local correction/learning tools and dictionary |
| `regresyon/` | Frame regression and video+GT OCR gate |
| `docs/` | Architecture and development notes |

## License

MIT. OCR models, PyTorch/CUDA, FFmpeg, and other third-party components may have separate licenses and distribution terms; model weights are not included in this repository.

# Troubleshooting

[Türkçe sorun giderme rehberi](SORUN-GIDERME.md) [TR] · **English [EN]**

## FFmpeg or FFprobe cannot be found

In a new PowerShell window, run:

```powershell
Get-Command ffmpeg
Get-Command ffprobe
ffmpeg -version
ffprobe -version
```

If either command is missing, add the FFmpeg installation directory to `PATH` and open a new terminal. If both tools are found but the video will not open, check that it plays in another player and verify the container/codec combination.

## OCR is not using the GPU

GPU OCR depends on CUDA-enabled PyTorch, the GPU driver, and packages in the same Python environment:

```powershell
py -3 -m pip show torch
py -3 -c "import torch; print(torch.__version__); print(torch.cuda.is_available())"
```

If the result is `False`, OCR falls back to the CPU; `--cpu` explicitly selects CPU. CUDA is not pinned by this repository. Install a compatible CUDA-enabled PyTorch build and check again with the same commands.

`--nvdec` is for video decoding; it does not show whether the OCR model uses the GPU. It is off by default and decoding falls back to the CPU if it fails. Check video decoding and the OCR device separately.

## Softsub output is unexpected or cannot be extracted

If FFprobe finds a subtitle track, the CLI tries to convert the first embedded track to SRT with FFmpeg instead of using OCR. It uses only the first track; the CLI cannot select another one. If the codec/container is unsupported, FFmpeg reports an error and there is no OCR fallback. If the source has no embedded track, the hardsub OCR path is used.

## The local UI does not open

- Read the error in the `arayuz.bat` window and check that Flask is installed in the same Python environment.
- If another application uses port 8765, close it or inspect the service using that port.
- The UI listens on `127.0.0.1:8765` on this computer; it is not available to other devices on the network.

Queue jobs are kept in server memory. If the server restarted, pending jobs are gone; UI job history contains only bounded diagnostic summaries and does not restart jobs.

## No output was written or an earlier output changed

`-o` is the full path to the target SRT file, not a directory. The hardsub OCR path creates a missing parent directory; direct softsub extraction through FFmpeg does not, so create the parent directory first for softsub. Rerunning the CLI with the same target overwrites the main SRT and same-name sidecars. Choose a new filename to preserve the previous output. If the `--review-pack` folder already exists, the CLI stops; back it up before removing it manually.

The UI creates a new `runs/...` folder for each job; check the run directory shown by the UI. If a QA image is missing, make sure the UI QA count is not 0 or check the CLI `--qa` option. The full CLI default is six samples; a limited run defaults to 0. The UI default is four.

## Privacy and shared files

`.stats.json` and `.hardsub2srt.json` may include a video name, size/modified time, or partial matching hash. A review pack contains SRT text and frame crops. Review attachments before sharing them; application network behavior does not control what an external sync service may copy from a synced folder.

For setup, see [Installation and first run](INSTALLATION-AND-FIRST-RUN.md). For the complete document list, see the [documentation map](README.en.md).

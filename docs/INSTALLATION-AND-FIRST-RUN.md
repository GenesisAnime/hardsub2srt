# Installation and first run

[Türkçe sürüm](KURULUM-VE-ILK-CALISTIRMA.md) [TR] · **English [EN]**

Windows is the primary user platform for this repository. Ubuntu is tested by GitHub Actions with Python 3.14; this does not mean that the complete video/OCR workflow is verified on Ubuntu. End-to-end use on macOS has not been verified here.

## Requirements

- Python 3.10 or newer. OCR runtime packages and CUDA compatibility depend on the Python and driver combination.
- FFmpeg and FFprobe, both available on `PATH`.
- Python packages listed in `requirements.txt`. This repository does not provide a locked environment or a packaged `.exe`.

The [official FFmpeg download page](https://ffmpeg.org/download.html) lists Windows build providers. The build you choose must include `ffmpeg.exe` and `ffprobe.exe`; add its `bin` directory to `PATH`. For GPU OCR, use the [PyTorch installation selector](https://pytorch.org/get-started/locally/) to choose a CUDA package that matches your Python and driver. `requirements.txt` does not select a CUDA wheel.

Check that the tools are available in PowerShell:

```powershell
py -3 --version
ffmpeg -version
ffprobe -version
```

## Python packages

From the repository root, run:

```powershell
py -3 -m pip install --upgrade pip
py -3 -m pip install -r requirements.txt
```

EasyOCR/PyTorch may download model files on first use. CUDA-enabled PyTorch and its driver setup depend on the machine; `requirements.txt` does not pin a CUDA wheel. If you expect GPU OCR, check it with the same Python interpreter:

```powershell
py -3 -c "import torch; print(torch.__version__); print(torch.cuda.is_available())"
```

If the GPU path is unavailable, use `--cpu` to run OCR on the CPU.

## Create your first SRT

```powershell
py -3 hardsub2srt.py "D:\LOCAL-ONLY-PLACEHOLDER\input.mkv" -o "D:\LOCAL-ONLY-PLACEHOLDER\output.srt"
```

`-o` is the full path to the target SRT file. Rerunning with the same target overwrites that SRT and its same-name sidecars; choose a new filename to preserve the previous output. The hardsub OCR path creates a missing parent directory. Direct softsub extraction through FFmpeg does not, so create the parent directory before using softsub with a new output folder.

For all CLI options, run `py -3 hardsub2srt.py --help`. To open the local UI, run `arayuz.bat` or `py -3 ui_server.py`, then open `http://127.0.0.1:8765` in a browser. Native file pickers are Windows-specific; on other systems, enter paths manually.

## Platform support

GitHub CI runs tests on Windows and Ubuntu with Python 3.14. It does not install an OCR model or test real video, CUDA, or GPU inference. Do not interpret CI results as verification of a real-media workflow. This repository does not claim end-to-end macOS support.

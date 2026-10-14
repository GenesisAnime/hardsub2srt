# Usage guide

[Türkçe rehber](KULLANIM-REHBERI.md) [TR] · **English [EN]**

## Understand the subtitle stream

- **Hardsub:** the subtitle is rendered into the video frames. If FFprobe does not find an embedded subtitle track, the tool uses OCR to read the visible text.
- **Softsub:** when FFprobe finds an embedded subtitle track, the tool tries to convert the first track to SRT with FFmpeg without running OCR. An unsupported codec/container causes an FFmpeg error; there is no hardsub OCR fallback. The CLI cannot select another track or translate it automatically.

Neither path translates subtitles. Hardsub OCR reads text from the image; softsub extraction copies the existing subtitle text. Translation requires a separate workflow.

![Flow diagram showing the hardsub OCR and softsub FFmpeg paths](images/subtitle-extraction-flow.en.svg)

## CLI examples

```powershell
# Standard OCR
py -3 hardsub2srt.py "D:\LOCAL-ONLY-PLACEHOLDER\input.mkv" -o "D:\LOCAL-ONLY-PLACEHOLDER\output.srt"

# Use the subtitle language; Turkish is the default, English is explicit
py -3 hardsub2srt.py "D:\LOCAL-ONLY-PLACEHOLDER\input.mkv" -o "D:\LOCAL-ONLY-PLACEHOLDER\output-en.srt" --lang en

# Force OCR to use the CPU
py -3 hardsub2srt.py "D:\LOCAL-ONLY-PLACEHOLDER\input.mkv" -o "D:\LOCAL-ONLY-PLACEHOLDER\output.srt" --cpu

# A short first run; QA montage is disabled by default for limited CLI runs
py -3 hardsub2srt.py "D:\LOCAL-ONLY-PLACEHOLDER\input.mkv" -o "D:\LOCAL-ONLY-PLACEHOLDER\limited-test.srt" --limit-seconds 60

# Include six QA samples in a full run; use --qa 0 to disable the montage
py -3 hardsub2srt.py "D:\LOCAL-ONLY-PLACEHOLDER\input.mkv" -o "D:\LOCAL-ONLY-PLACEHOLDER\output.srt" --qa 6

# Also create a local human-review pack for hardsub OCR
py -3 hardsub2srt.py "D:\LOCAL-ONLY-PLACEHOLDER\input.mkv" -o "D:\LOCAL-ONLY-PLACEHOLDER\output.srt" --review-pack
```

For a full CLI run, the default is six QA samples; a run limited with `--limit-seconds` disables QA by default. `--qa 0` disables the montage in either case. The UI has a separate QA setting: four samples by default, or off when set to 0.

`--nvdec` tries FFmpeg's NVDEC video-decoding path. It is separate from the PyTorch CUDA device used by the OCR model. NVDEC is off by default; decoding falls back to the CPU if it fails.

Use `--ust-bant` to write upper-band text to a separate `_ust.srt`. `--ust-ana` adds upper-band cues to the main SRT and should only be used after checking the video's behavior. Run `py -3 hardsub2srt.py --help` for all options.

## Local web UI

Run `arayuz.bat` or `py -3 ui_server.py` to open the local Flask UI at `http://127.0.0.1:8765`. You can select multiple videos. Each job writes to a new `runs/video-<name>/<timestamp>_<job-id>/` folder and does not overwrite an earlier run folder.

The queue has one worker and is held in memory; pending jobs are not restored after a server restart. Native Windows file/folder pickers are Windows-specific; enter paths manually on other systems.

## Outputs and reruns

In the CLI, `-o` is the SRT output target. A hardsub run writes the main SRT and may also write `_ekran.srt`, `.stats.json`, `.hardsub2srt.json`, and other JSON sidecars tied to the same filename. Rerunning to the same target overwrites existing CLI outputs; back them up first or choose another target.

`--review-pack` also creates a `<SRT-stem>.review-pack/` directory. The CLI stops if that directory already exists and never overwrites it. Direct softsub extraction does not create a review pack because it has no OCR frame-to-cue matching. A review pack may contain SRT text and subtitle-frame crops. If you manually send a review package to an AI chat, its contents go to the AI provider you choose; the application does not call the provider's API.

Choose EasyOCR languages to match the subtitle: the CLI defaults to `tr`; use `en` for English or `tr,en` only for genuinely bilingual subtitles. The UI defaults to creating a review pack. Its human review page flags low confidence, unexpected scripts, suspicious token shapes, very short cues, and high characters per second. These are review warnings, not automatic corrections. Low-confidence cues are also retried by the second engine.

For review and learning workflows, see [Review bundle: human OCR review](REVIEW-BUNDLE.md) [EN], [AI OCR visual review](AI-OCR-VISUAL-REVIEW.en.md) [EN], and [Seed import](SEED-IMPORT.md) [EN].

## Batch launchers

`cikar.bat` accepts multiple video files or folders. `toplu.bat` also accepts a folder or multiple files. Both write SRT files beside the launcher, put four QA montages in a `qa/` folder beside it, and pass `--qa 4`. SRT names are derived from video basenames, so videos with the same basename in different folders may collide and overwrite each other's output.

`toplu.bat` records successful files by basename and file size in `kosuldu.list` beside the launcher, then skips matching entries on later runs. This does not verify that file contents are identical. Use `toplu.bat --yeni` as the first argument to disable skipping; the list is kept.

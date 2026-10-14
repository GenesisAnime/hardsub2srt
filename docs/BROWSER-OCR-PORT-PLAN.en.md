# Browser OCR port — technical plan and decision gates

This is a technical plan, not an implemented feature. It describes how far a pure browser client hosted on GitHub Pages could reproduce work that currently runs locally with Python, FFmpeg, and PyTorch.

## Technical boundary

File selection requires user interaction: `<input type=file>` / the File API exposes the contents of the selected `File`; a web app does not obtain an arbitrary absolute Windows path. WebGPU is not a permission prompt for file selection. It is available only in secure contexts; `navigator.gpu` may be absent, and `requestAdapter()` may return `null` when no suitable adapter is available. The browser, operating system, driver, and device determine adapter selection. None of these APIs grants permission to run Python, PowerShell, `ffmpeg.exe`, CUDA Torch, or another local subprocess.

GitHub Pages is static web hosting. It does not run a Python OCR engine or a persistent worker. The VDS API also does not process video frames or use the GPU on a user's computer. A true browser-only experience would require reimplementing OCR, decoding, and preprocessing/postprocessing in JavaScript/WASM/WebGPU, then downloading and running models in the browser.

## Component mapping

| Current Python layer | Existing implementation | Work required for the browser |
|---|---|---|
| Video metadata/decoder | `hardsub2srt.py`: `run_ffprobe`, `grab_gray`; ffprobe/ffmpeg child processes | Reimplement demuxing, seeking, and decoding. WebCodecs works only for supported media/container/codec combinations; a WASM FFmpeg/demuxer alternative may be needed for broader compatibility. Copying the entire video into IndexedDB is unnecessary and expensive. |
| Frame sampling/band detection | `detect_style`, `detect_style_upper`, `scan_band`, candidate band/stripe functions; OpenCV/NumPy | Port algorithms and heuristics to JS/TypedArray or OpenCV.js/WASM. Read frames through canvas/WebCodecs and preserve sampling, tolerances, and the time base. |
| Crop/resize/threshold | `grab_band`, `binarize_white`, contrast/line preparation | Use OpenCV.js or JS/WASM pixel kernels. Fixture comparisons are needed for pixel format, color space, interpolation, and threshold equivalence. |
| OCR model | EasyOCR/PyTorch primary engine; RapidOCR/ONNX secondary engine paths | Inventory actual model files and licenses; export or convert a compatible graph to ONNX and validate shapes, opsets, and operators. `onnxruntime-web` can use WASM CPU or WebGPU; WASM has broader operator coverage, while WebGPU supports a narrower subset. A WASM fallback is needed when GPU support is absent or incompatible. |
| OCR decode/postprocess/consensus | `ocr_lines`, batch engine wrappers, Turkish normalization, `_merge_readings`, voting | Port model output decoding, confidence conversion, line ordering, Turkish correction, dictionaries, and two-engine consensus to JavaScript. A gate must compare WebGPU graph output with current OCR results. |
| Segmentation and SRT timing | Block merging/noise separation and `main`; `srt_format.py` | Port cue start/end generation from frame timestamps, gaps, merge/split behavior, and SRT formatting. A small pure formatting function such as `fmt_ts` is easy to port; it is not a pipeline port by itself. |
| QA/editor/sidecars | CLI JSON, UI results, QA montage/PNG, and `vtt-qa.py` | Reimplement QA crops with Canvas, plus SRT/VTT parsing, alignment/CER, editing/saving, and JSON reports in browser-local JS. File System Access API can be optional, with downloadable Blob output as a fallback. |

### Model and runtime notes

Source code in `hardsub2srt.py` contains RapidOCR/ONNX Runtime and EasyOCR/PyTorch paths. This does not prove that model files are included in the repository, browser-compatible, or equivalent in a browser. Model weights are not distributed here. Before a spike, measure model locations, downloaded graph/license, ORT operator support, input shape, output decoder, size, and RAM/VRAM use. Do not assume a model runs in a browser merely because it is ONNX.

## Minimum prototype

Decide with a narrow technical spike, not a full UI port:

1. Choose a short, licensed local test video outside the repository, a fixed subtitle band, and known start/end times.
2. Validate an ONNX export or existing ONNX weights for one compatible model; run it with ONNX Runtime Web WASM. Compare input/output tensors with Python using a small fixture.
3. Decode only the required timestamped frames using browser `<video>`/Canvas or WebCodecs; run crop/resize/threshold and one OCR model.
4. Convert one OCR line into a timestamped cue and SRT download. Produce a CER/alignment report for a fixed ground-truth fixture.
5. If the fixed fixture passes, try the WebGPU execution provider; retain WASM when unsupported. Measure cold start, model download, video decode, OCR latency, peak memory, and cancel/pause behavior.

Automatic band discovery, two-engine consensus, all Turkish correction/learning features, ASS, QA montage, and the current editor remain out of scope for the first spike. Do not present it as a full-featured product until it passes the quality gate.

## Effort and main risks

- **Narrow spike: medium effort.** It needs one model artifact/compatible opset, a small codec sample, and a trusted ground-truth fixture. Success is not guaranteed; model export or decoding may fail and require another model.
- **Feature parity: high effort and risk.** Container/codec support, seek timestamp differences, OpenCV pixel equivalence, Turkish postprocessing, multi-engine decisions, WebGPU operator/device differences, high RAM/VRAM use, and cancellation when the user closes the page all need solutions.
- **Distribution:** large model and WASM files may hit Pages bandwidth/cache and browser memory limits. HTTPS secure context is required; WebGPU is not available on every browser/device. WASM CPU fallback may be slow on unsupported devices.
- **Accuracy:** OCR model CER alone is insufficient. Fixed samples should also measure SRT cue timing, low-confidence cues, missing/extra cues, special characters, and CSS/video color conversion. Compare every change with gates similar to `regresyon/gt_gate.py` and fixed-frame comparisons.

## Recommendation

If the product should retain users' existing CUDA/GPU and Python engine, the lowest-risk product path is **Pages for downloads/documentation, a local Python worker for OCR, and a separate explicitly consented VDS API for derived metrics**. Browser-only OCR should proceed as a separate research prototype; permission to select a video in a browser is not permission to run a local executable.

## Primary sources

- [W3C WebGPU specification](https://www.w3.org/TR/webgpu/) — secure context, adapter selection, and `requestAdapter()` behavior.
- [MDN File API](https://developer.mozilla.org/en-US/docs/Web/API/File_API/Using_files_from_web_applications) — user-selected files in web apps.
- [MDN WebCodecs API](https://developer.mozilla.org/en-US/docs/Web/API/WebCodecs_API) — browser media decode/encode API and codec limits.
- [ONNX Runtime Web](https://onnxruntime.ai/docs/tutorials/web/) and [execution provider/operator support](https://onnxruntime.ai/docs/execution-providers/) — WASM/WebGPU execution paths and operator coverage.
- [ONNX Runtime WebGPU](https://onnxruntime.ai/docs/tutorials/web/ep-webgpu.html) and [deployment](https://onnxruntime.ai/docs/tutorials/web/deploy.html) — WebGPU and web deployment details.
- [FFmpeg.wasm performance](https://ffmpegwasm.netlify.app/docs/performance/) and [FAQ](https://ffmpegwasm.netlify.app/docs/faq/) — WASM performance and resource costs.
- [GitHub Pages: What is GitHub Pages?](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages) — the static hosting model.

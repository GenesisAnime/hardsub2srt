# Contribution guidelines

[Türkçe katkı kuralları](CONTRIBUTING.md) [TR] · **English [EN]**

## Language and documentation

- Keep Turkish and English documentation in separate files: write each Turkish document in Turkish and each English counterpart in English. Keep established technical terms such as OCR, CLI, GPU, branch, commit, pull request, and CI in their usual form.
- `README.md` is the Turkish landing page; `README.en.md` is the English landing page. Keep translated documents in separate files and label the language in the documentation map. Existing technical documents may remain in their original language unless an English or Turkish counterpart is listed.
- Distinguish implemented behavior, measured results, plans, and blocked work. Do not claim support, performance, or accuracy unless code, tests, or measurements provide evidence.
- Check commands and options against current source. Do not include personal paths, usernames, media names, or machine-specific details in examples; use explicit placeholders and synthetic data.

## Privacy and copyright

Do not add the following to this repository:

- Videos, anime frames, QA screenshots, or other copyrighted media.
- Raw `.srt`/`.vtt` files, dialogue, sentence-level OCR examples, or personal subtitle files.
- Usernames, real absolute local paths, private hostnames, tokens, passwords, API keys, or identifying hashes derived from them.

Do not attach OCR output or run artifacts to issues or pull requests. If sharing metrics, inspect the fields and share only the necessary privacy-reviewed summary. An AI OCR or translation response is a proposal; it does not replace a human decision, and the application does not automatically send content to an AI provider.

## Development and verification

Local tests use the same lightweight dependency set as GitHub Actions; OCR engines and model weights are not installed for tests. In a clean Python test environment:

```powershell
py -3 -m pip install --upgrade pip
py -3 -m pip install pytest flask numpy opencv-python-headless
py -3 -m pytest -q -rs
```

CI runs `python -m pytest -q -rs` on Ubuntu and Windows with Python 3.14. Tests cover synthetic/runtime behavior; they do not measure real-video OCR quality or GPU inference. CI does not install OCR runtime dependencies and uses `opencv-python-headless`. For a changed Python file, `py -3 -m py_compile <file>` can be used as an additional syntax check; it does not replace tests. For OCR behavior changes, use an authorized video and trusted time-aligned reference in addition to relevant tests. State clearly when such a measurement was not run.

## Dictionary, licensing, and commit messages

`kullanici-sozlugu.txt` should contain word-level corrections only. Do not add dialogue or contextual subtitle text. Explain in the pull request why each rule is general and safe.

The code is MIT-licensed. OCR libraries/model weights, PyTorch/CUDA, FFmpeg, and other third-party components have separate licensing and distribution terms. Do not add model weights to the repository.

Write commit titles and any body in concise Turkish, as requested by the project owner; established technical jargon may remain in English. In PowerShell, do not pass Turkish characters to `git commit -m`. Save the message as UTF-8 and use `git commit -F <file>`.

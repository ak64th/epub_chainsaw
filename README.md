# EPUB tooling

Two helper scripts wrap the `ebooklib` workflow for breaking an EPUB into editable assets, editing them in plain text, and assembling a standards-compliant EPUB that passes `epubcheck`.

## 1. Extract an EPUB

```
source .venv/bin/activate
python extract_epub.py --epub /path/to/origin.epub --out-dir /path/to/out
```

- `--force` removes the output directory first when you need to re-run the extraction.
- Text chapters become `.txt` files under `/text/**` and **only** contain raw text (line breaks preserved exactly). Metadata that cannot live inside the txt file (title, language, any SVG/IMG blocks, reference paths) is saved in parallel JSON sidecars under `/text_meta/**`. For convenience, the untouched XHTML source for each chapter also lives under `/text_xhtml/**` so you can quickly compare the extracted txt output with the original markup.
- Non-text assets keep their original binary format under `/images`, `/styles`, or `/misc`. The top-level `metadata.json` captures the manifest, TOC, and spine entries while pointing each chapter to its txt/meta pair.

Each txt file always adds a blank line between the title line and the rest of the body, so manual edits stay consistent.

## 2. Rebuild an EPUB (original text)

```
source .venv/bin/activate
python build_epub.py \
  --in-dir /path/to/out \
  --output /path/to/new.epub \
  --title "Optional Title Override" \
  --author "Author Name" \
  --epubcheck /usr/bin/epubcheck
```

- `build_epub.py` reads `metadata.json`, recreates each chapter’s XHTML from the txt/meta pair, and ensures all injected markup is legal (e.g., SVG placeholders are converted to `<img>` tags with `alt` text). This avoids the validation errors present in the source EPUB.
- Pass `--epubcheck` (plus optional `--epubcheck-args …`) to validate automatically after writing the file. The script fails fast if `epubcheck` returns non-zero so you can correct issues immediately.
- You can re-run `epubcheck` manually at any time: `epubcheck rebuilt.epub`.

## 3. Rebuild an EPUB with translations

```
source .venv/bin/activate
python build_translated_epub.py \
  --in-dir /path/to/out \
  --translations /path/to/translations \
  --output /path/to/new-translated.epub \
  --language zh \
  --epubcheck /usr/bin/epubcheck
```

- Place translations in the directory you pass via `--translations`. Files follow the pattern `<chapter>_translated.txt` (e.g., `0001__translated.txt`) and **must** keep the same number of lines (blank lines included) as the original txt file. The script compares line-by-line and substitutes translated lines when available; if a translation is missing or the counts mismatch, it logs a warning and falls back to the original text so the build never crashes.
- `--language` defaults to `zh` but you can override it for other locales.
- The tool reuses the placeholder metadata, so SVG/image blocks continue to convert into safe markup.

## Typical workflow

1. `extract_epub.py --epub origin.epub --out-dir extracted_origin --force`
2. Edit any files under `extracted_origin/text` (and their JSON sidecars if you need to tweak placeholder metadata). You can also update CSS or images.
3. `build_epub.py --in-dir extracted_origin --output rebuilt_origin.epub --epubcheck /usr/bin/epubcheck`
   or `build_translated_epub.py --in-dir extracted_origin --translations translated --output rebuilt_translated.epub --language zh --epubcheck /usr/bin/epubcheck`
4. Inspect the generated EPUB or repeat steps 2-3 until the content looks right.


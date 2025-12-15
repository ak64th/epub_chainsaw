# EPUB tooling

Two helper scripts wrap the `ebooklib` workflow for breaking an EPUB into editable assets and assembling them back into a valid EPUB file.

## 1. Extract an EPUB

```
source .venv/bin/activate
python extract_epub.py --epub /path/to/origin.epub --out-dir /path/to/out
```

- `--force` removes the output directory first when you need to re-run the extraction.
- Assets land under `text/`, `styles/`, `images/`, or `misc/` within the output directory, and `metadata.json` captures the manifest, original spine, and TOC. Edit the XHTML/CSS files directly if you need to tweak translations or formatting.

## 2. Rebuild an EPUB

```
source .venv/bin/activate
python build_epub.py \
  --in-dir /path/to/out \
  --output /path/to/new.epub \
  --title "Optional Title Override" \
  --author "Author Name"
```

- `build_epub.py` reads `metadata.json` to recreate the manifest and only needs overrides when you want to change high-level metadata such as title, identifier, language, or authors.
- Pass `--epubcheck /path/to/epubcheck` to validate the generated EPUB automatically. Any extra arguments for the checker can follow via `--epubcheck-args --mode exp`.
- You can also run epubcheck manually later: `epubcheck /path/to/new.epub`.

## Typical workflow

1. `extract_epub.py` splits the original file.
2. Update text/style assets as needed.
3. `build_epub.py` creates the final EPUB and optionally runs epubcheck to confirm it is well-formed.


#!/usr/bin/env python3
"""Build an EPUB using translated plain-text chapters when available."""

from __future__ import annotations

import argparse
import logging
import pathlib
import sys
from typing import Any, Dict, List, Optional

from ebooklib import epub

from build_epub import (
    load_metadata,
    load_chapter_meta,
    render_text_with_extras,
    configure_metadata,
    build_spine,
    build_toc,
    run_epubcheck,
    create_item,
)

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in-dir", required=True, help="Directory produced by extract_epub.py")
    parser.add_argument(
        "--translations",
        required=True,
        help="Directory containing translated txt files (e.g., 0001__translated.txt)",
    )
    parser.add_argument("--output", required=True, help="Destination EPUB file path")
    parser.add_argument("--title", help="Override title stored in metadata.json")
    parser.add_argument("--identifier", help="Override identifier stored in metadata.json")
    parser.add_argument(
        "--language",
        default="zh",
        help="Language code to set on the output EPUB (default: zh)",
    )
    parser.add_argument(
        "--author",
        action="append",
        help="Author name. Repeat for multiple authors. Defaults to metadata.json entries.",
    )
    parser.add_argument("--epubcheck", help="Path to epubcheck CLI binary to validate the output file")
    parser.add_argument(
        "--epubcheck-args",
        nargs=argparse.REMAINDER,
        help="Arguments passed to epubcheck after the binary and before the EPUB path",
    )
    return parser.parse_args()


def normalized_translation_name(chapter_txt_path: pathlib.Path) -> str:
    stem = chapter_txt_path.stem
    return f"{stem}_translated.txt"


def load_translation(
    translations_dir: pathlib.Path, chapter_txt_path: pathlib.Path
) -> Optional[List[str]]:
    translation_file = translations_dir / normalized_translation_name(chapter_txt_path)
    if not translation_file.exists():
        return None
    try:
        content = translation_file.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        logger.warning("Failed to decode translation %s: %s", translation_file, exc)
        return None
    # Keep exact line structure, including blank lines
    return content.splitlines()


def apply_translation(original_text: str, translated_lines: List[str]) -> str:
    original_lines = original_text.splitlines()
    if len(original_lines) != len(translated_lines):
        logger.warning(
            "Translation line count mismatch (original=%s, translated=%s). Skipping translation.",
            len(original_lines),
            len(translated_lines),
        )
        return original_text
    merged_lines = []
    for orig, trans in zip(original_lines, translated_lines):
        # Allow blank translated lines; otherwise prefer translated content.
        merged_lines.append(trans if trans.strip() or not orig.strip() else orig)
    return "\n".join(merged_lines)


def build_translated_chapters(
    book: epub.EpubBook,
    metadata: Dict[str, Any],
    base_dir: pathlib.Path,
    translations_dir: pathlib.Path,
    default_language: str,
) -> Dict[str, epub.EpubHtml]:
    chapters: Dict[str, epub.EpubHtml] = {}
    for item in metadata.get("items", []):
        if item.get("type") != "document":
            continue
        relative_path = item.get("relative_path")
        meta_relative_path = item.get("meta_relative_path")
        if not relative_path or not meta_relative_path:
            logger.warning("Skipping document missing paths: %s", item.get("file_name"))
            continue
        txt_path = base_dir / relative_path
        meta_path = base_dir / meta_relative_path
        if not txt_path.exists():
            logger.warning("Missing chapter txt: %s", txt_path)
            continue
        try:
            original_text = txt_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            logger.warning("UTF-8 decode error in %s; using replacement characters.", txt_path)
            original_text = txt_path.read_text(encoding="utf-8", errors="replace")
        translation_lines = load_translation(translations_dir, txt_path)
        is_translated = translation_lines is not None
        if translation_lines is not None:
            text_to_use = apply_translation(original_text, translation_lines)
        else:
            text_to_use = original_text
        chapter_meta = load_chapter_meta(meta_path)
        if is_translated:
            chapter_lang = default_language
        else:
            chapter_lang = (
                chapter_meta.get("lang")
                or item.get("lang")
                or default_language
            )
        html_content = render_text_with_extras(
            text_to_use, chapter_meta.get("placeholders", [])
        )
        chapter = epub.EpubHtml(
            title=chapter_meta.get("title") or item.get("title") or item.get("file_name"),
            file_name=item.get("file_name"),
            lang=chapter_lang,
        )
        chapter.content = html_content
        item_id = item.get("id")
        if item_id:
            chapter.id = item_id
        properties = chapter_meta.get("properties") or item.get("properties")
        if properties:
            chapter.properties = properties
        book.add_item(chapter)
        chapters[item["file_name"]] = chapter
    return chapters


def add_support_items(
    book: epub.EpubBook,
    metadata: Dict[str, Any],
    base_dir: pathlib.Path,
    book_language: str,
) -> None:
    for item in metadata.get("items", []):
        if item.get("type") == "document":
            continue
        create_item(book, base_dir, item, book_language)


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    base_dir = pathlib.Path(args.in_dir).expanduser().resolve()
    translations_dir = pathlib.Path(args.translations).expanduser().resolve()
    output_path = pathlib.Path(args.output).expanduser().resolve()

    if not translations_dir.exists():
        raise SystemExit(f"Translations directory does not exist: {translations_dir}")

    metadata = load_metadata(base_dir)
    book = epub.EpubBook()
    book_language = args.language or metadata.get("language") or "zh"
    configure_metadata(
        book,
        metadata,
        argparse.Namespace(
            identifier=args.identifier,
            title=args.title,
            language=book_language,
            author=args.author,
            output=args.output,
        ),
    )
    chapters = build_translated_chapters(
        book, metadata, base_dir, translations_dir, book_language
    )
    add_support_items(book, metadata, base_dir, book_language)

    if metadata.get("toc"):
        book.toc = build_toc(metadata["toc"], chapters)
    else:
        book.toc = list(chapters.values())

    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    if metadata.get("spine"):
        book.spine = build_spine(metadata["spine"], chapters)
    else:
        book.spine = ["nav", *chapters.values()]

    epub.write_epub(str(output_path), book, {})
    logger.info("Wrote EPUB to %s", output_path)

    if args.epubcheck:
        additional = args.epubcheck_args or []
        run_epubcheck(args.epubcheck, additional, output_path)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)


#!/usr/bin/env python3
"""Rebuild an EPUB from extracted assets and optional validation with epubcheck."""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
from typing import Any, Dict, List

from ebooklib import epub

METADATA_FILENAME = "metadata.json"

TEXTUAL_TYPES = {"document"}
SKIP_TYPES = {"ncx", "container", "navigation"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in-dir", required=True, help="Directory produced by extract_epub.py")
    parser.add_argument("--output", required=True, help="Destination EPUB file path")
    parser.add_argument("--title", help="Override title stored in metadata.json")
    parser.add_argument("--identifier", help="Override identifier stored in metadata.json")
    parser.add_argument("--language", help="Override language stored in metadata.json")
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


def load_metadata(base: pathlib.Path) -> Dict[str, Any]:
    metadata_path = base / METADATA_FILENAME
    if not metadata_path.exists():
        raise SystemExit(f"metadata file not found: {metadata_path}")
    with metadata_path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def create_item(book: epub.EpubBook, base: pathlib.Path, item_meta: Dict[str, Any], book_language: str) -> Any:
    file_path = base / item_meta["relative_path"]
    if not file_path.exists():
        raise SystemExit(f"Missing asset for {item_meta['file_name']}: {file_path}")
    raw = file_path.read_bytes()
    item_id = item_meta.get("id") or item_meta.get("file_name")
    item_type = item_meta.get("type", "other")

    properties = list(item_meta.get("properties") or [])

    if item_type in TEXTUAL_TYPES:
        chapter = epub.EpubHtml(
            title=item_meta.get("title") or item_meta.get("file_name"),
            file_name=item_meta.get("file_name"),
            lang=item_meta.get("lang") or book_language,
        )
        chapter.content = raw
        if item_id:
            chapter.id = item_id
        if properties:
            chapter.properties = properties
        book.add_item(chapter)
        return chapter

    if item_type in SKIP_TYPES:
        return None

    media_type = item_meta.get("media_type") or "application/octet-stream"
    generic = epub.EpubItem(
        uid=item_id,
        file_name=item_meta.get("file_name"),
        media_type=media_type,
        content=raw,
    )
    if properties:
        generic.properties = properties
    book.add_item(generic)
    return generic


def build_chapters(book: epub.EpubBook, metadata: Dict[str, Any], base: pathlib.Path) -> Dict[str, epub.EpubHtml]:
    chapters: Dict[str, epub.EpubHtml] = {}
    language = metadata.get("language") or "en"
    for item in metadata.get("items", []):
        created = create_item(book, base, item, language)
        if isinstance(created, epub.EpubHtml):
            chapters[item["file_name"]] = created
    return chapters


def build_toc(entries: List[Dict[str, Any]], chapters: Dict[str, epub.EpubHtml]) -> List[Any]:
    def _convert(entry: Dict[str, Any]) -> Any:
        children = [ _convert(child) for child in entry.get("children", []) ]
        kind = entry.get("kind")

        if kind == "section":
            section = epub.Section(entry.get("title") or "Section")
            return (section, tuple(children))
        if kind == "html":
            file_name = entry.get("file_name")
            chapter = chapters.get(file_name)
            if chapter is None:
                raise SystemExit(f"TOC references unknown chapter: {file_name}")
            if entry.get("title"):
                chapter.title = entry["title"]
            if children:
                return (chapter, tuple(children))
            return chapter
        if kind == "link":
            link = epub.Link(
                entry.get("href") or "",
                entry.get("title") or entry.get("href") or "Link",
                entry.get("uid") or entry.get("href") or entry.get("title") or "link",
            )
            if children:
                return (link, tuple(children))
            return link
        if children:
            return tuple(children)
        return entry.get("title") or "Untitled"

    return [_convert(entry) for entry in entries] if entries else []


def build_spine(spine_entries: List[str], chapters: Dict[str, epub.EpubHtml]) -> List[Any]:
    id_lookup = {
        getattr(chapter, "id", None): chapter
        for chapter in chapters.values()
        if getattr(chapter, "id", None)
    }
    spine: List[Any] = []
    for entry in spine_entries:
        if entry == "nav":
            spine.append("nav")
            continue
        chapter = chapters.get(entry) or id_lookup.get(entry)
        if not chapter:
            raise SystemExit(f"Spine references unknown chapter: {entry}")
        spine.append(chapter)
    return spine


def configure_metadata(book: epub.EpubBook, metadata: Dict[str, Any], args: argparse.Namespace) -> None:
    identifier = args.identifier or metadata.get("identifier") or "book-id"
    title = args.title or metadata.get("title") or pathlib.Path(args.output).stem
    language = args.language or metadata.get("language") or "en"
    authors = args.author or metadata.get("authors") or ["Unknown author"]

    book.set_identifier(identifier)
    book.set_title(title)
    book.set_language(language)
    for author in authors:
        book.add_author(author)


def run_epubcheck(epubcheck: str, additional_args: List[str], output_path: pathlib.Path) -> None:
    cmd = [epubcheck, *(additional_args or []), str(output_path)]
    print(f"Running epubcheck: {' '.join(cmd)}")
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    print(result.stdout)
    if result.returncode != 0:
        raise SystemExit(f"epubcheck failed with exit code {result.returncode}")


def main() -> None:
    args = parse_args()
    base_dir = pathlib.Path(args.in_dir).expanduser().resolve()
    output_path = pathlib.Path(args.output).expanduser().resolve()

    metadata = load_metadata(base_dir)
    book = epub.EpubBook()
    configure_metadata(book, metadata, args)
    chapters = build_chapters(book, metadata, base_dir)

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
    print(f"Wrote EPUB to {output_path}")

    if args.epubcheck:
        additional = args.epubcheck_args or []
        run_epubcheck(args.epubcheck, additional, output_path)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)


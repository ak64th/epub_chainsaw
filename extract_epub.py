#!/usr/bin/env python3
"""Utility to extract EPUB contents into text/style/image folders."""

from __future__ import annotations

import argparse
import copy
import json
import pathlib
import re
import shutil

import ebooklib
from ebooklib import epub
from lxml import etree
from lxml import html as lhtml

from epub_types import (
    ChapterMetadata,
    ItemRecord,
    Metadata,
    PlaceholderBlock,
    TocEntry,
    TocNode,
)


METADATA_FILENAME = "metadata.json"
TEXT_CONTENT_ROOT = pathlib.Path("text")
TEXT_META_ROOT = pathlib.Path("text_meta")
TEXT_XHTML_ROOT = pathlib.Path("text_xhtml")

PLACEHOLDER_TEMPLATE = "[[EPUB_HTML_BLOCK_{:04d}]]"
PLACEHOLDER_PATTERN = re.compile(r"\[\[EPUB_HTML_BLOCK_\d{4}\]\]")

TYPE_NAMES = {
    ebooklib.ITEM_DOCUMENT: "document",
    ebooklib.ITEM_IMAGE: "image",
    ebooklib.ITEM_STYLE: "style",
    ebooklib.ITEM_NAVIGATION: "navigation",
    ebooklib.ITEM_COVER: "cover",
    ebooklib.ITEM_VECTOR: "vector",
    ebooklib.ITEM_FONT: "font",
    ebooklib.ITEM_VIDEO: "video",
    ebooklib.ITEM_AUDIO: "audio",
    ebooklib.ITEM_SCRIPT: "script",
    ebooklib.ITEM_SMIL: "smil",
    ebooklib.ITEM_UNKNOWN: "unknown",
}

CATEGORY_BY_TYPE = {
    "document": "text",
    "navigation": "text",
    "style": "styles",
    "image": "images",
    "cover": "images",
    "vector": "images",
}

SPECIAL_MEDIA_TAGS = {"svg", "img", "image", "object", "iframe"}
TEXT_IGNORE_TAGS = {"desc", "title"}


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for EPUB extraction."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epub", required=True, help="Path to the source EPUB file.")
    parser.add_argument(
        "--out-dir",
        required=True,
        help="Directory where assets and metadata.json will be written.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete the output directory first if it already exists.",
    )
    return parser.parse_args()


def ensure_output_dir(target: pathlib.Path, force: bool) -> None:
    """Ensure output directory exists, optionally removing it first if force is True."""
    if target.exists():
        if not force and any(target.iterdir()):
            raise SystemExit(
                f"Output directory {target} exists and is not empty. "
                "Use --force to overwrite."
            )
        if force:
            shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)


def first_meta(values: list[tuple[str, dict[str, str]]]) -> str | None:
    """Extract the first metadata value from a list of (value, attrs) tuples."""
    return values[0][0] if values else None


def sanitize_relative_path(name: str, fallback: str) -> pathlib.Path:
    """Sanitize a relative path by removing dangerous components like '..'."""
    pure = pathlib.PurePosixPath(name or fallback)
    filtered = [part for part in pure.parts if part not in ("..", "", ".")]
    if not filtered:
        filtered = [fallback]
    return pathlib.Path(*filtered)


def serialize_toc(entries: list[TocEntry] | tuple[TocEntry, ...]) -> list[TocNode]:
    """Convert EPUB table of contents to JSON-serializable format."""
    if not entries:
        return []
    return [_serialize_toc_entry(entry) for entry in entries]


def _serialize_toc_entry(entry: TocEntry) -> TocNode:
    node: TocNode
    if isinstance(entry, tuple) and len(entry) == 2:
        node = _serialize_toc_entry(entry[0])
        node["children"] = [_serialize_toc_entry(item) for item in entry[1]]
        return node

    node = {"children": []}
    if isinstance(entry, epub.Section):
        node.update({"kind": "section", "title": entry.title})
    elif isinstance(entry, epub.Link):
        node.update(
            {
                "kind": "link",
                "title": entry.title,
                "href": entry.href,
                "uid": entry.uid,
            }
        )
    elif isinstance(entry, epub.EpubHtml):
        node.update(
            {
                "kind": "html",
                "file_name": entry.file_name,
                "title": entry.title,
            }
        )
    else:
        node.update({"kind": "unknown", "repr": repr(entry)})
    return node


def serialize_spine(
    spine_entries: list[epub.EpubHtml | tuple[str, str] | str],
) -> list[str]:
    """Convert EPUB spine to a list of string identifiers."""
    serialized = []
    for entry in spine_entries:
        if isinstance(entry, epub.EpubHtml):
            serialized.append(entry.file_name)
        elif isinstance(entry, tuple):
            serialized.append(entry[0])
        else:
            serialized.append(str(entry))
    return serialized


def classify_item(item: epub.EpubItem) -> str:
    """Classify an EPUB item by type (document, image, style, etc.)."""
    if isinstance(item, epub.EpubNcx):
        return "ncx"
    if isinstance(item, epub.EpubNav):
        return "navigation"
    return TYPE_NAMES.get(item.get_type(), "other")


def _local_name(elem: etree._Element) -> str:
    # pylint: disable=no-member
    return etree.QName(elem).localname.lower()


def _node_has_special_media(elem: etree._Element) -> bool:
    for node in elem.iter():
        if not isinstance(node.tag, str):
            continue
        if _local_name(node) in SPECIAL_MEDIA_TAGS:
            return True
    return False


def _has_meaningful_text(elem: etree._Element) -> bool:
    if elem.text and elem.text.strip():
        if _local_name(elem) not in TEXT_IGNORE_TAGS:
            return True
    for node in elem.iter():
        if node is elem:
            continue
        if node.text and node.text.strip():
            if _local_name(node) not in TEXT_IGNORE_TAGS:
                return True
        if node.tail and node.tail.strip():
            return True
    return False


def _should_extract_special_block(elem: etree._Element) -> bool:
    if not isinstance(elem.tag, str):
        return False
    if not _node_has_special_media(elem):
        return False
    if _has_meaningful_text(elem):
        return False
    parent = elem.getparent()
    while parent is not None and isinstance(parent.tag, str):
        if _node_has_special_media(parent) and not _has_meaningful_text(parent):
            return False
        parent = parent.getparent()
    return True


def _sanitize_special_block(elem: etree._Element) -> str:
    cloned = copy.deepcopy(elem)
    for node in cloned.iter():
        if not isinstance(node.tag, str):
            continue
        lname = _local_name(node)
        attrs = list(node.attrib.items())
        for key, value in attrs:
            new_key = key
            if lname == "svg":
                lowered = key.lower()
                if lowered == "viewbox":
                    new_key = "viewBox"
                elif lowered == "preserveaspectratio":
                    new_key = "preserveAspectRatio"
            if new_key != key:
                del node.attrib[key]
                node.attrib[new_key] = value
    # type: ignore[assignment]
    html_string: str = lhtml.tostring(cloned, encoding="unicode", method="html")
    return html_string


def _normalize_text_output(text: str) -> str:
    text = text.replace("\r\n", "\n")
    text = "\n".join(line.rstrip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _ensure_blank_after_title(text: str) -> str:
    if not text:
        return text
    lines = text.splitlines()
    if not lines:
        return text
    title = lines[0].rstrip()
    rest = "\n".join(lines[1:])
    rest = rest.lstrip("\n")
    if rest:
        return f"{title}\n\n{rest}"
    return f"{title}\n"


def extract_text_and_extras(raw_content: bytes) -> tuple[str, list[PlaceholderBlock]]:
    """Extract plain text and special HTML blocks (images, SVG) from chapter content."""
    try:
        tree = lhtml.fromstring(raw_content)
    except etree.ParserError:
        return raw_content.decode("utf-8", errors="ignore"), []
    body = tree.find("body")
    if body is None:
        body = tree
    extras: list[PlaceholderBlock] = []
    nodes = list(body.iter())
    placeholder_counter = 1
    for elem in nodes:
        if not isinstance(elem.tag, str):
            continue
        if elem.getparent() is None and elem is not body:
            continue
        if not _should_extract_special_block(elem):
            continue
        placeholder = PLACEHOLDER_TEMPLATE.format(placeholder_counter)
        placeholder_counter += 1
        sanitized = _sanitize_special_block(elem)
        extras.append({"placeholder": placeholder, "html": sanitized})
        parent = elem.getparent()
        if parent is None:
            continue
        replacement = etree.Element("p")
        replacement.text = placeholder
        replacement.tail = elem.tail
        parent.replace(elem, replacement)
    text_content = body.text_content() or ""  # type: ignore[attr-defined]
    text_content = _normalize_text_output(text_content)
    text_content = PLACEHOLDER_PATTERN.sub(
        lambda match: f"\n\n{match.group(0)}\n\n", text_content
    )
    text_content = re.sub(r"\n{3,}", "\n\n", text_content).strip()
    text_content = _ensure_blank_after_title(text_content)
    return text_content, extras


def extract_items(book: epub.EpubBook, out_dir: pathlib.Path) -> list[ItemRecord]:  # pylint: disable=too-many-locals
    """Extract all items from EPUB to files and return metadata records."""
    records: list[ItemRecord] = []
    for idx, item in enumerate(book.get_items()):
        type_name = classify_item(item)
        category = CATEGORY_BY_TYPE.get(type_name, "misc")
        safe_rel = sanitize_relative_path(
            item.file_name or f"item_{idx}.bin", f"item_{idx}.bin"
        )
        properties = list(getattr(item, "properties", []) or [])
        content = item.get_content()
        meta_rel_path: pathlib.Path | None = None

        if type_name == "document":
            relative_path = (TEXT_CONTENT_ROOT / safe_rel).with_suffix(".txt")
            meta_rel_path = (TEXT_META_ROOT / safe_rel).with_suffix(".meta.json")
            xhtml_rel_path = (TEXT_XHTML_ROOT / safe_rel).with_suffix(".xhtml")
            text_output, extras = extract_text_and_extras(content)
            text_target = out_dir / relative_path
            text_target.parent.mkdir(parents=True, exist_ok=True)
            text_target.write_text(text_output, encoding="utf-8")
            xhtml_target = out_dir / xhtml_rel_path
            xhtml_target.parent.mkdir(parents=True, exist_ok=True)
            xhtml_target.write_bytes(content)
            chapter_meta: ChapterMetadata = {
                "title": getattr(item, "title", None),
                "lang": getattr(item, "lang", None),
                "properties": properties,
                "placeholders": extras,
                "reference_xhtml": xhtml_rel_path.as_posix(),
            }
            meta_target = out_dir / meta_rel_path
            meta_target.parent.mkdir(parents=True, exist_ok=True)
            meta_target.write_text(
                json.dumps(chapter_meta, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        else:
            relative_path = pathlib.Path(category) / safe_rel
            target_path = out_dir / relative_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(content)

        records.append(
            {
                "id": getattr(item, "get_id", lambda: None)()
                or getattr(item, "id", None),
                "file_name": item.file_name or safe_rel.as_posix(),
                "media_type": getattr(item, "media_type", None),
                "type": type_name,
                "category": category,
                "title": getattr(item, "title", None),
                "lang": getattr(item, "lang", None),
                "properties": properties,
                "relative_path": relative_path.as_posix(),
                "meta_relative_path": meta_rel_path.as_posix()
                if meta_rel_path
                else None,
            }
        )
    return records


def build_metadata(book: epub.EpubBook, items: list[ItemRecord]) -> Metadata:
    """Build complete metadata dictionary from EPUB book and extracted items."""
    metadata: Metadata = {
        "identifier": first_meta(book.get_metadata("DC", "identifier")),
        "title": first_meta(book.get_metadata("DC", "title")),
        "language": first_meta(book.get_metadata("DC", "language")),
        "authors": [value for value, _ in book.get_metadata("DC", "creator")],
        "spine": serialize_spine(book.spine),
        "toc": serialize_toc(book.toc),
        "items": items,
    }
    return metadata


def dump_metadata(metadata: Metadata, out_dir: pathlib.Path) -> None:
    """Write metadata to metadata.json file."""
    metadata_path = out_dir / METADATA_FILENAME
    with metadata_path.open("w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=2, ensure_ascii=False)


def main() -> None:
    """Main entry point for extracting EPUB contents."""
    args = parse_args()
    epub_path = pathlib.Path(args.epub).expanduser().resolve()
    out_dir = pathlib.Path(args.out_dir).expanduser().resolve()
    ensure_output_dir(out_dir, args.force)

    book = epub.read_epub(str(epub_path))
    items = extract_items(book, out_dir)
    metadata = build_metadata(book, items)
    dump_metadata(metadata, out_dir)
    print(f"Extracted {len(items)} items to {out_dir}")
    print(f"Metadata written to {out_dir / METADATA_FILENAME}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Utility to extract EPUB contents into text/style/image folders."""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
from typing import Any, Dict, List

import ebooklib
from ebooklib import epub


METADATA_FILENAME = "metadata.json"

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


def parse_args() -> argparse.Namespace:
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
    if target.exists():
        if not force and any(target.iterdir()):
            raise SystemExit(
                f"Output directory {target} exists and is not empty. "
                "Use --force to overwrite."
            )
        if force:
            shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)


def first_meta(values: List[Any]) -> Any:
    return values[0][0] if values else None


def sanitize_relative_path(name: str, fallback: str) -> pathlib.Path:
    pure = pathlib.PurePosixPath(name or fallback)
    filtered = [part for part in pure.parts if part not in ("..", "", ".")]
    if not filtered:
        filtered = [fallback]
    return pathlib.Path(*filtered)


def serialize_toc(entries: Any) -> List[Dict[str, Any]]:
    if not entries:
        return []
    return [_serialize_toc_entry(entry) for entry in entries]


def _serialize_toc_entry(entry: Any) -> Dict[str, Any]:
    node: Dict[str, Any]
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


def serialize_spine(spine_entries: List[Any]) -> List[str]:
    serialized = []
    for entry in spine_entries:
        if isinstance(entry, epub.EpubHtml):
            serialized.append(entry.file_name)
        elif isinstance(entry, tuple):
            serialized.append(entry[0])
        else:
            serialized.append(str(entry))
    return serialized


def classify_item(item: Any) -> str:
    if isinstance(item, epub.EpubNcx):
        return "ncx"
    if isinstance(item, epub.EpubNav):
        return "navigation"
    return TYPE_NAMES.get(item.get_type(), "other")


def extract_items(book: epub.EpubBook, out_dir: pathlib.Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for idx, item in enumerate(book.get_items()):
        type_name = classify_item(item)
        category = CATEGORY_BY_TYPE.get(type_name, "misc")
        safe_rel = sanitize_relative_path(
            item.file_name or f"item_{idx}.bin", f"item_{idx}.bin"
        )
        relative_path = pathlib.Path(category) / safe_rel
        content = item.get_content()
        target_path = out_dir / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(content)

        properties = list(getattr(item, "properties", []) or [])
        if (
            type_name == "document"
            and b"<svg" in content.lower()
            and "svg" not in properties
        ):
            properties.append("svg")

        records.append(
            {
                "id": getattr(item, "get_id", lambda: None)() or getattr(item, "id", None),
                "file_name": item.file_name or safe_rel.as_posix(),
                "media_type": getattr(item, "media_type", None),
                "type": type_name,
                "category": category,
                "title": getattr(item, "title", None),
                "lang": getattr(item, "lang", None),
                "properties": properties,
                "relative_path": relative_path.as_posix(),
            }
        )
    return records


def build_metadata(book: epub.EpubBook, items: List[Dict[str, Any]]) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "identifier": first_meta(book.get_metadata("DC", "identifier")),
        "title": first_meta(book.get_metadata("DC", "title")),
        "language": first_meta(book.get_metadata("DC", "language")),
        "authors": [value for value, _ in book.get_metadata("DC", "creator")],
        "spine": serialize_spine(book.spine),
        "toc": serialize_toc(book.toc),
        "items": items,
    }
    return metadata


def dump_metadata(metadata: Dict[str, Any], out_dir: pathlib.Path) -> None:
    metadata_path = out_dir / METADATA_FILENAME
    with metadata_path.open("w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=2, ensure_ascii=False)


def main() -> None:
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


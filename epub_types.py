"""Type definitions for EPUB metadata and structures."""

from __future__ import annotations

from typing import TypedDict, Union
from ebooklib import epub


# JSON-serializable structures for metadata and extracted content


class PlaceholderBlock(TypedDict):
    """A placeholder entry for extracted HTML blocks (images, SVG, etc.)."""

    placeholder: str
    html: str


class ChapterMetadata(TypedDict, total=False):
    """Metadata for a single chapter extracted from EPUB."""

    title: str | None
    lang: str | None
    properties: list[str]
    placeholders: list[PlaceholderBlock]
    reference_xhtml: str


class ItemRecord(TypedDict, total=False):
    """Record of an extracted EPUB item (chapter, image, style, etc.)."""

    id: str | None
    file_name: str
    media_type: str | None
    type: str
    category: str
    title: str | None
    lang: str | None
    properties: list[str]
    relative_path: str
    meta_relative_path: str | None


class TocNode(TypedDict, total=False):
    """Serialized table of contents node."""

    kind: str
    title: str | None
    href: str | None
    uid: str | None
    file_name: str | None
    repr: str | None
    children: list[TocNode]


class Metadata(TypedDict, total=False):
    """Complete EPUB metadata bundle."""

    identifier: str | None
    title: str | None
    language: str | None
    authors: list[str]
    spine: list[str]
    toc: list[TocNode]
    items: list[ItemRecord]


# Type aliases for ebooklib structures before serialization

TocEntry = Union[
    epub.Section,
    epub.Link,
    epub.EpubHtml,
    tuple["TocEntry", tuple["TocEntry", ...]],
]

# Reconstructed TOC element after building from serialized TocNode
TocElement = Union[
    epub.Section,
    epub.Link,
    epub.EpubHtml,
    tuple[Union[epub.Section, epub.Link, epub.EpubHtml], tuple[object, ...]],
    str,
]

SpineEntry = Union[epub.EpubHtml, tuple[str, str], str]

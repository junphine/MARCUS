"""Remote docling-serve wrapper (drop-in alternative to the local Docling wrapper).

Mirrors the public API of :mod:`app.modules.docling_segmentation_wrapper`
(``get_complete_segments``, ``get_segments_with_bbox_docling``,
``extract_picture_segments_from_pdf``, ``segments_exist``,
``get_highlighted_segment_image``, ``cleanup_caches``) but performs document
conversion **remotely** against a docling-serve instance instead of running
Docling locally.

The actual HTTP client already lives in :mod:`app.modules.parser.docling`
(see :class:`~app.modules.parser.docling.client.DoclingRawClient`). This module
only adapts the remote bundle into the same on-disk layout the local wrapper
produces (``uploads/segments/{stem}/{stem}.{md,json}`` +
``images/picture_N_segmented.png``), so routers can switch backends purely on
the presence of the ``DOCLING_ENDPOINT`` environment variable.
"""

from __future__ import annotations

import asyncio
import base64
import glob
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, status

from app.config import SEGMENTS_DIR
from app.modules.parser.docling import DoclingRawClient
from app.modules.parser.docling.manifest import select_main_json, select_main_md

# Thread-safe dictionary with lock for concurrent access
segment_info_lock = threading.Lock()
stored_segment_info = {}

# Cache for PDF metadata
pdf_cache_lock = threading.Lock()
pdf_metadata_cache = {}


def measure_performance(func):
    """Decorator to measure function execution time."""

    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        print(f"{func.__name__} took {end_time - start_time:.2f} seconds")
        return result

    return wrapper


@contextmanager
def thread_safe_dict_access(lock):
    """Context manager for thread-safe dictionary access."""
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def _run_coroutine(coro):
    """Run an async coroutine to completion from a (possibly) sync context.

    The docling-serve client is async; the legacy routers call the wrapper
    functions synchronously. When we are not inside a running event loop we
    simply ``asyncio.run`` it; when we are (e.g. an async FastAPI route), the
    coroutine is run on a dedicated thread so it gets its own loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def get_pdf_hash(filepath: str) -> str:
    """Generate a hash of the PDF file for caching purposes."""
    hasher = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def create_output_directory(filepath: str) -> str:
    """Create (or reuse) the output directory for a PDF."""
    if not filepath.lower().endswith(".pdf"):
        raise ValueError(f"Expected a PDF file, got: {filepath}")

    file_path = Path(filepath)
    file_stem = file_path.stem
    output_directory = Path(SEGMENTS_DIR) / file_stem

    if output_directory.exists():
        return str(output_directory)

    output_directory.mkdir(mode=0o755, exist_ok=True)
    return str(output_directory)


REF_HEADER_PAT = re.compile(
    r"^\s*(references|bibliography|works cited|参考文献|引用文献)\s*$",
    re.IGNORECASE,
)


def _ref_str(node: Any) -> str:
    """Normalize a Docling reference (``{"$ref": "#/texts/0"}`` or a bare
    string) to its string form."""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        v = node.get("$ref") or node.get("ref")
        if isinstance(v, str):
            return v
    return ""


def _remove_reference_section(doc: Dict[str, Any]) -> Dict[str, Any]:
    """JSON-level equivalent of the local wrapper's ``remove_reference_section``.

    Removes the references/bibliography section (and everything below it up to
    the next heading of the same or higher level) from ``texts`` and
    ``body.children`` so downstream text extraction does not include citations.
    """
    texts = doc.get("texts")
    if not isinstance(texts, list):
        return doc

    ref_start_idx = None
    for i, item in enumerate(texts):
        if not isinstance(item, dict):
            continue
        if (
            str(item.get("label") or "").lower() == "section_header"
            and REF_HEADER_PAT.match(str(item.get("text") or ""))
        ):
            ref_start_idx = i
            break
    if ref_start_idx is None:
        return doc

    def _level(item: Any) -> int:
        if not isinstance(item, dict):
            return 99
        try:
            return int(item.get("level") or 1)
        except (TypeError, ValueError):
            return 99

    to_remove = set()
    header = texts[ref_start_idx]
    to_remove.add(str(header.get("self_ref") or ""))
    header_level = _level(header)

    for item in texts[ref_start_idx + 1 :]:
        if (
            isinstance(item, dict)
            and str(item.get("label") or "").lower() == "section_header"
            and _level(item) <= header_level
        ):
            break
        if isinstance(item, dict):
            to_remove.add(str(item.get("self_ref") or ""))

    doc["texts"] = [
        t for t in texts if str((t or {}).get("self_ref") or "") not in to_remove
    ]
    body = doc.get("body")
    if isinstance(body, dict):
        body["children"] = [
            c for c in (body.get("children") or []) if _ref_str(c) not in to_remove
        ]
    return doc


def _picture_uri(picture: Dict[str, Any]) -> str:
    image = picture.get("image")
    if not isinstance(image, dict):
        return ""
    return str(image.get("uri") or "")


def _decode_data_uri_bytes(uri: str) -> Optional[bytes]:
    """Decode a ``data:image/...;base64,...`` URI into raw image bytes."""
    try:
        head, payload = uri.split(",", 1)
    except ValueError:
        return None
    if ";base64" not in head:
        return None
    try:
        return base64.b64decode(payload, validate=False)
    except (ValueError, TypeError):
        return None


def _picture_bytes(raw_dir: Path, picture: Dict[str, Any]) -> Optional[bytes]:
    """Return the raw image bytes for a picture, or ``None`` if unavailable.

    Handles both ``data:`` URIs (embedded) and artifact-relative file paths
    (the default ``image_export_mode=referenced`` layout). Remote ``http(s)``
    and unsafe/absolute paths yield ``None``.
    """
    uri = _picture_uri(picture)
    if uri.startswith("data:"):
        return _decode_data_uri_bytes(uri)
    if not uri or uri.startswith(("http://", "https://")) or os.path.isabs(uri):
        return None
    try:
        base = raw_dir.resolve(strict=False)
        candidate = (raw_dir / uri).resolve(strict=False)
        candidate.relative_to(base)
    except (OSError, ValueError, RuntimeError):
        return None
    try:
        return candidate.read_bytes() if candidate.is_file() else None
    except OSError:
        return None


def _picture_provenance(picture: Dict[str, Any]) -> Tuple[List[int], int]:
    """Extract ``(bbox, page_number)`` from a picture item's provenance."""
    bbox: List[int] = [0, 0, 0, 0]
    page_number = 0
    provs = picture.get("prov")
    if isinstance(provs, list) and provs and isinstance(provs[0], dict):
        prov = provs[0]
        b = prov.get("bbox")
        if isinstance(b, dict) and all(k in b for k in ("l", "t", "r", "b")):
            bbox = [int(b["l"]), int(b["t"]), int(b["r"]), int(b["b"])]
        pn = prov.get("page_no")
        if pn is not None:
            try:
                page_number = int(pn)
            except (TypeError, ValueError):
                page_number = 0
    return bbox, page_number


def _rewrite_markdown_images(markdown_text: str, pictures: List[Any]) -> str:
    """Point markdown image references at the extracted segment images."""
    if not markdown_text:
        return markdown_text

    # docling's local exporter emits ``<!-- image -->`` placeholders; replace
    # them with segment-relative links (parity with the local wrapper).
    image_index = 0

    def _image_replacer(_match):
        nonlocal image_index
        image_index += 1
        return f"![Figure {image_index}](images/picture_{image_index}_segmented.png)"

    markdown_text = re.sub(r"<!--\s*image\s*-->", _image_replacer, markdown_text)

    # docling-serve typically references the artifact files directly, so map
    # those relative URIs onto the segment images as well.
    for n, picture in enumerate(pictures, start=1):
        if not isinstance(picture, dict):
            continue
        uri = _picture_uri(picture)
        if not uri or uri.startswith(("data:", "http://", "https://")) or os.path.isabs(
            uri
        ):
            continue
        markdown_text = markdown_text.replace(uri, f"images/picture_{n}_segmented.png")
    return markdown_text


def extract_picture_segments_from_pdf(
    file_path: str, progress_callback=None
) -> Tuple[str, List[Dict[str, Any]], str]:
    """Extract picture/figure segments from a PDF using remote docling-serve.

    Downloads the docling-serve bundle, materializes its pictures as segment
    images, and writes the ``<stem>.md`` / ``<stem>.json`` twin the routers
    read, in the same layout the local wrapper produces.

    Returns:
        Tuple of ``(output_directory, segments_metadata, markdown_text)``.
    """
    base_directory = create_output_directory(file_path)
    all_segments_dir = os.path.join(base_directory, "images")
    os.makedirs(all_segments_dir, exist_ok=True)

    segments_metadata: List[Dict[str, Any]] = []

    original_filename = Path(file_path).name
    seg_safe_name = re.sub(r"[^\w.-]", "_", original_filename)
    pdf_stem = os.path.splitext(seg_safe_name)[0]
    md_path = os.path.join(base_directory, f"{pdf_stem}.md")
    json_path = os.path.join(base_directory, f"{pdf_stem}.json")

    # The canonical upload name makes docling-serve name the bundle's main
    # JSON ``<stem>.json``, which is what ``select_main_json`` looks for.
    upload_filename = f"{pdf_stem}.pdf"

    raw_dir = Path(tempfile.mkdtemp(prefix="docling_serve_"))
    try:
        _run_coroutine(
            DoclingRawClient().download_into(
                raw_dir, Path(file_path), upload_filename=upload_filename
            )
        )

        main_json = select_main_json(raw_dir, Path(upload_filename))
        doc = json.loads(main_json.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            raise RuntimeError(f"Docling bundle JSON is not an object: {main_json}")

        main_md = select_main_md(raw_dir, Path(upload_filename))
        markdown_text = (
            main_md.read_text(encoding="utf-8") if main_md and main_md.is_file() else ""
        )

        # Strip the references section (parity with the local wrapper).
        _remove_reference_section(doc)

        pictures = doc.get("pictures") if isinstance(doc.get("pictures"), list) else []
        picture_counter = 0
        for picture in pictures:
            if not isinstance(picture, dict):
                continue
            picture_counter += 1

            try:
                image_bytes = _picture_bytes(raw_dir, picture)
                segment_filename = f"picture_{picture_counter}_segmented.png"
                segment_path = os.path.join(all_segments_dir, segment_filename)

                if image_bytes is not None:
                    with open(segment_path, "wb") as fh:
                        fh.write(image_bytes)

                bbox, page_number = _picture_provenance(picture)
                segment_metadata = {
                    "segment_id": f"picture-{picture_counter}",
                    "filename": segment_filename,
                    "path": os.path.join("images", segment_filename),
                    "full_path": segment_path,
                    "pageNumber": page_number,
                    "bbox": bbox,
                    "segmentNumber": picture_counter,
                    "pdfFilename": os.path.basename(file_path),
                }
                segments_metadata.append(segment_metadata)

                if progress_callback:
                    progress_callback(picture_counter, "unknown")
            except Exception as e:
                print(f"Error processing picture {picture_counter}: {str(e)}")
                continue

        markdown_text = _rewrite_markdown_images(markdown_text, pictures)

        if md_path:
            with open(md_path, "w", encoding="utf-8") as md_file:
                md_file.write(markdown_text)
        if json_path:
            with open(json_path, "w", encoding="utf-8") as json_file:
                json.dump(doc, json_file, ensure_ascii=False, indent=2)

        return base_directory, segments_metadata, markdown_text
    finally:
        shutil.rmtree(raw_dir, ignore_errors=True)


@measure_performance
def get_segments_with_bbox_docling(
    file_path: str, progress_callback=None
) -> Tuple[str, List[Dict[str, Any]], str]:
    """Extract segments from PDF using remote docling-serve."""
    if os.getenv("DOCLING_ENDPOINT"):
        return extract_picture_segments_from_pdf(file_path, progress_callback)
    else:
        from .docling_segmentation_wrapper import extract_picture_segments_from_pdf as extract_picture_segments_from_pdf_local
        return extract_picture_segments_from_pdf_local(file_path, progress_callback)



def get_complete_segments(path_to_pdf: str, collect_all: bool = True) -> Dict[str, Any]:
    """Complete pipeline for extracting segments using remote docling-serve."""
    try:
        pdf_hash = get_pdf_hash(path_to_pdf)

        with thread_safe_dict_access(pdf_cache_lock):
            if pdf_hash in pdf_metadata_cache:
                cached_result = pdf_metadata_cache[pdf_hash]
                if os.path.exists(cached_result["segment_directory"]):
                    return cached_result

        segments_exist_result = segments_exist(path_to_pdf)
        segments_already_exist = segments_exist_result[0]
        existing_path = segments_exist_result[1]

        if segments_already_exist:
            segment_directory = existing_path
            pdf_filename = os.path.basename(path_to_pdf)

            with thread_safe_dict_access(segment_info_lock):
                segments_metadata = stored_segment_info.get(pdf_filename, [])

            if not segments_metadata:
                segments_metadata = load_segment_metadata(segment_directory)

            if not segments_metadata:
                all_segments_dir = os.path.join(segment_directory, "images")
                if os.path.exists(all_segments_dir):
                    segments_metadata = generate_basic_metadata(
                        all_segments_dir, pdf_filename
                    )

            if segments_metadata:
                with thread_safe_dict_access(segment_info_lock):
                    stored_segment_info[pdf_filename] = segments_metadata
        else:
            segment_directory, segments_metadata, mkdown = get_segments_with_bbox_docling(
                path_to_pdf
            )

            pdf_filename = os.path.basename(path_to_pdf)
            with thread_safe_dict_access(segment_info_lock):
                stored_segment_info[pdf_filename] = segments_metadata

        all_segments_dir = os.path.join(segment_directory, "images")
        result = {
            "segment_directory": segment_directory,
            "segments_existed": segments_already_exist,
            "all_segments_directory": (
                all_segments_dir if os.path.exists(all_segments_dir) else None
            ),
            "segments_info": segments_metadata,
        }

        with thread_safe_dict_access(pdf_cache_lock):
            pdf_metadata_cache[pdf_hash] = result

        return result

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error during segmentation: {str(e)}",
        )


def generate_basic_metadata(
    all_segments_dir: str, pdf_filename: str
) -> List[Dict[str, Any]]:
    """Generate basic metadata from existing segment files."""
    segments_metadata = []

    for segment_file in sorted(os.listdir(all_segments_dir)):
        if segment_file.endswith("_segmented.png"):
            parts = segment_file.split("_")
            if len(parts) >= 3 and parts[0] == "picture":
                try:
                    idx = int(parts[1])
                    segment_id = f"picture-{idx}"

                    segment_metadata = {
                        "segment_id": segment_id,
                        "filename": segment_file,
                        "path": os.path.join("images", segment_file),
                        "full_path": os.path.join(all_segments_dir, segment_file),
                        "pageNumber": 0,
                        "segmentNumber": idx + 1,
                        "pdfFilename": pdf_filename,
                        "bbox": [0, 0, 1000, 1000],
                    }

                    segments_metadata.append(segment_metadata)
                except (ValueError, IndexError):
                    continue

    return segments_metadata


@measure_performance
def get_highlighted_segment_image(segment_id: str, pdf_filename: str) -> str:
    """Generate a highlighted segment image (kept for API compatibility).

    Note: This is a simplified placeholder implementation, matching the local
    wrapper, since the segmentation concept is Docling-picture based.
    """
    cache_key = f"{pdf_filename}_{segment_id}"
    cache_file = os.path.join(SEGMENTS_DIR, f"cache_highlighted_{cache_key}.png")

    if os.path.exists(cache_file):
        if time.time() - os.path.getmtime(cache_file) < 3600:  # 1 hour
            return cache_file

    try:
        from PIL import Image, ImageDraw

        placeholder = Image.new("RGB", (200, 200), (255, 255, 255))
        draw = ImageDraw.Draw(placeholder)
        draw.text((10, 100), f"Segment: {segment_id}", fill=(0, 0, 0))
        draw.text((10, 130), f"From: {pdf_filename}", fill=(100, 100, 100))
        draw.rectangle((10, 10, 190, 190), outline=(0, 255, 0), width=2)
        placeholder.save(cache_file)
        return cache_file
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Segment image not found: {segment_id}",
        )


def segments_exist(filepath: str) -> Tuple[bool, Optional[str]]:
    """Check if segments already exist for a PDF file."""
    if not filepath.lower().endswith(".pdf"):
        raise ValueError(f"Expected a PDF file, got: {filepath}")

    file_stem = Path(filepath).stem
    segments_base = Path(SEGMENTS_DIR)

    matching_dirs = [d for d in segments_base.glob(f"{file_stem}*") if d.is_dir()]

    if not matching_dirs:
        return False, None

    base_directory = max(matching_dirs, key=lambda d: d.stat().st_mtime)

    all_segments_dir = base_directory / "images"
    if all_segments_dir.exists() and all_segments_dir.is_dir():
        if any(all_segments_dir.glob("*_segmented.png")):
            return True, str(base_directory)

    segment_dirs = list(base_directory.glob("*_segments"))
    for segment_dir in segment_dirs:
        if any(segment_dir.glob("*_segmented.png")):
            return True, str(base_directory)

    return False, None


def save_segment_metadata(
    directory: str, segments_metadata: List[Dict[str, Any]]
) -> None:
    """Save segment metadata with an atomic write."""
    try:
        metadata_file = os.path.join(directory, "segments_metadata.json")
        temp_file = metadata_file + ".tmp"

        os.makedirs(directory, exist_ok=True)

        with open(temp_file, "w") as f:
            json.dump(segments_metadata, f, indent=2)

        os.replace(temp_file, metadata_file)

        if os.path.exists(metadata_file):
            file_size = os.path.getsize(metadata_file)
            print(f"Successfully saved metadata file ({file_size} bytes)")

    except Exception as e:
        print(f"Error saving segment metadata: {str(e)}")
        if os.path.exists(temp_file):
            os.remove(temp_file)


def load_segment_metadata(directory: str) -> List[Dict[str, Any]]:
    """Load segment metadata with error handling and caching."""
    metadata_file = os.path.join(directory, "segments_metadata.json")

    if os.path.exists(metadata_file):
        try:
            file_size = os.path.getsize(metadata_file)
            if file_size > 100 * 1024 * 1024:  # 100MB limit
                print(f"Warning: Metadata file too large ({file_size} bytes)")
                return []

            with open(metadata_file, "r") as f:
                data = json.load(f)
                print(f"Successfully loaded metadata ({len(data)} segments)")
                return data

        except json.JSONDecodeError as e:
            print(f"Warning: JSON decode error loading segment metadata: {str(e)}")
        except Exception as e:
            print(f"Warning: Failed to load segment metadata: {str(e)}")

    return []


def cleanup_caches():
    """Clean up caches and temporary files."""
    global stored_segment_info, pdf_metadata_cache

    with thread_safe_dict_access(segment_info_lock):
        stored_segment_info.clear()

    with thread_safe_dict_access(pdf_cache_lock):
        pdf_metadata_cache.clear()

    cache_pattern = os.path.join(SEGMENTS_DIR, "cache_highlighted_*.png")
    for cache_file in glob.glob(cache_pattern):
        try:
            if time.time() - os.path.getmtime(cache_file) > 3600:  # 1 hour old
                os.remove(cache_file)
        except Exception:
            pass


__all__ = [
    "cleanup_caches",
    "create_output_directory",
    "extract_picture_segments_from_pdf",
    "generate_basic_metadata",
    "get_complete_segments",
    "get_highlighted_segment_image",
    "get_pdf_hash",
    "get_segments_with_bbox_docling",
    "load_segment_metadata",
    "save_segment_metadata",
    "segments_exist",
]

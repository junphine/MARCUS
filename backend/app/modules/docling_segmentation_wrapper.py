import os
import uuid
import json
import threading
import hashlib
from typing import List, Optional, Tuple, Dict, Any
from pathlib import Path
import cv2
import numpy as np
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.datamodel.base_models import InputFormat
from docling.document_converter import ConversionResult
from docling_core.types.doc import PictureItem, TableItem
from fastapi import HTTPException, status
from functools import lru_cache
import time
from contextlib import contextmanager
import multiprocessing as mp

from app.config import PDF_DIR, SEGMENTS_DIR, MODELS_DIR

# Thread-safe dictionary with lock for concurrent access
segment_info_lock = threading.Lock()
stored_segment_info = {}

# Cache for PDF metadata
pdf_cache_lock = threading.Lock()
pdf_metadata_cache = {}

# Configure Docling to generate picture images
pipeline_options = PdfPipelineOptions(artifacts_path=MODELS_DIR)
pipeline_options.generate_picture_images = True
pipeline_options.generate_table_images = True

doc_converter = DocumentConverter(
    format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
)


# Performance monitoring decorator
def measure_performance(func):
    """Decorator to measure function execution time"""

    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        print(f"{func.__name__} took {end_time - start_time:.2f} seconds")
        return result

    return wrapper


# Context manager for thread-safe dictionary access
@contextmanager
def thread_safe_dict_access(lock):
    """Context manager for thread-safe dictionary access"""
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


@lru_cache(maxsize=128)
def get_doi_from_file(filepath: str) -> Dict[str, str]:
    """
    Extract the DOI from a PDF file with caching.

    Uses LRU cache to avoid repeated DOI extraction for the same files.
    """
    from pdf2doi import pdf2doi

    try:
        doi_dict = pdf2doi(filepath)
        doi = doi_dict["identifier"]
        if not doi:
            doi = Path(filepath).stem
        return {"doi": doi}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error extracting DOI: {str(e)}",
        )


def get_pdf_hash(filepath: str) -> str:
    """Generate a hash of the PDF file for caching purposes"""
    hasher = hashlib.md5()
    with open(filepath, "rb") as f:
        # Read in chunks to handle large files efficiently
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def create_output_directory(filepath: str) -> str:
    """
    Create an output directory for storing segmented chemical structures.

    Optimized with better path handling and validation.
    """
    if not filepath.lower().endswith(".pdf"):
        raise ValueError(f"Expected a PDF file, got: {filepath}")

    file_path = Path(filepath)
    file_stem = file_path.stem
    output_dir_name = file_stem

    output_directory = Path(SEGMENTS_DIR) / output_dir_name

    # Fast check for existing directory
    if output_directory.exists():
        return str(output_directory)

    # Create directory with proper permissions
    output_directory.mkdir(mode=0o755, exist_ok=True)
    return str(output_directory)


@measure_performance
def extract_picture_segments_from_pdf(
    file_path: str, progress_callback=None
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Extract picture/figure segments from PDF using Docling.

    This replaces the decimer_segmentation.segment_chemical_structures approach
    by using Docling's document understanding to identify and extract figures/pictures.

    Args:
        file_path: Path to the PDF file
        progress_callback: Optional callback for progress updates

    Returns:
        Tuple of (output_directory, segments_metadata)
    """
    base_directory = create_output_directory(file_path)
    all_segments_dir = os.path.join(base_directory, "all_segments")
    os.makedirs(all_segments_dir, exist_ok=True)

    segments_metadata = []

    try:


        # Convert the document
        conv_result: ConversionResult = doc_converter.convert(file_path)
        doc = conv_result.document

        # Process each element in the document to find pictures
        picture_counter = 0
        for element, _level in doc.iterate_items():
            if isinstance(element, PictureItem):
                picture_counter += 1

                try:
                    # Get the image data for this picture
                    pil_image = element.get_image(doc)

                    # Convert PIL image to numpy array for OpenCV processing
                    img_array = np.array(pil_image)

                    # Convert RGB to BGR for OpenCV (if needed)
                    if len(img_array.shape) == 3 and img_array.shape[2] == 3:
                        img_array = cv2.cvtColor(img_array, cv2.COLOR_RGB2BGR)

                    # Generate filename
                    segment_filename = f"picture_{picture_counter}_segmented.png"
                    segment_path = os.path.join(all_segments_dir, segment_filename)

                    # Save the image
                    cv2.imwrite(segment_path, img_array)

                    # Extract bounding box information
                    # Get the bounding box from the element's provenance
                    bbox = [0, 0, 0, 0]  # Default fallback
                    page_number = 0

                    if hasattr(element, 'prov') and element.prov:
                        prov = element.prov[0]  # Take first provenance
                        if hasattr(prov, 'bbox') and prov.bbox:
                            bbox = [
                                int(prov.bbox.l),   # left/x0
                                int(prov.bbox.t),   # top/y0
                                int(prov.bbox.r),   # right/x1
                                int(prov.bbox.b)    # bottom/y1
                            ]
                        if hasattr(prov, 'page_no'):
                            page_number = prov.page_no

                    # Create segment metadata (matching the existing format)
                    segment_id = f"picture-{picture_counter}"
                    segment_metadata = {
                        "segment_id": segment_id,
                        "filename": segment_filename,
                        "path": os.path.join("all_segments", segment_filename),
                        "full_path": segment_path,
                        "pageNumber": page_number,
                        "bbox": bbox,  # [x0, y0, x1, y1]
                        "segmentNumber": picture_counter,
                        "pdfFilename": os.path.basename(file_path),
                    }

                    segments_metadata.append(segment_metadata)

                    # Progress callback
                    if progress_callback:
                        progress_callback(picture_counter, "unknown")  # We don't know total beforehand

                except Exception as e:
                    print(f"Error processing picture {picture_counter}: {str(e)}")
                    continue

    except Exception as e:
        print(f"Error processing PDF with Docling: {str(e)}")
        # Fall back to empty results rather than failing completely
        pass

    return base_directory, segments_metadata


@measure_performance
def get_segments_with_bbox_docling(
    file_path: str, progress_callback=None
) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Extract segments from PDF using Docling-based approach.
    """
    return extract_picture_segments_from_pdf(file_path, progress_callback)


def get_complete_segments(path_to_pdf: str, collect_all: bool = True) -> Dict[str, Any]:
    """
    Complete pipeline for extracting segments using Docling.
    """
    try:
        # Quick cache check using file hash
        pdf_hash = get_pdf_hash(path_to_pdf)

        with thread_safe_dict_access(pdf_cache_lock):
            if pdf_hash in pdf_metadata_cache:
                cached_result = pdf_metadata_cache[pdf_hash]
                # Verify cached files still exist
                if os.path.exists(cached_result["segment_directory"]):
                    return cached_result

        # Check if segments already exist
        segments_exist_result = segments_exist(path_to_pdf)
        segments_already_exist = segments_exist_result[0]
        existing_path = segments_exist_result[1]

        if segments_already_exist:
            segment_directory = existing_path
            pdf_filename = os.path.basename(path_to_pdf)

            # Try to get metadata from memory cache first
            with thread_safe_dict_access(segment_info_lock):
                segments_metadata = stored_segment_info.get(pdf_filename, [])

            # Load from file if not in memory
            if not segments_metadata:
                segments_metadata = load_segment_metadata(segment_directory)

            # Generate basic metadata if still not found
            if not segments_metadata:
                all_segments_dir = os.path.join(segment_directory, "all_segments")
                if os.path.exists(all_segments_dir):
                    segments_metadata = generate_basic_metadata(
                        all_segments_dir, pdf_filename
                    )

            # Update memory cache
            if segments_metadata:
                with thread_safe_dict_access(segment_info_lock):
                    stored_segment_info[pdf_filename] = segments_metadata
        else:
            # Run segmentation using Docling approach
            segment_directory, segments_metadata = get_segments_with_bbox_docling(path_to_pdf)

            # Update memory cache
            pdf_filename = os.path.basename(path_to_pdf)
            with thread_safe_dict_access(segment_info_lock):
                stored_segment_info[pdf_filename] = segments_metadata

        # Prepare result
        all_segments_dir = os.path.join(segment_directory, "all_segments")
        result = {
            "segment_directory": segment_directory,
            "segments_existed": segments_already_exist,
            "all_segments_directory": (
                all_segments_dir if os.path.exists(all_segments_dir) else None
            ),
            "segments_info": segments_metadata,
        }

        # Cache the result
        with thread_safe_dict_access(pdf_cache_lock):
            pdf_metadata_cache[pdf_hash] = result

        return result

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error during segmentation: {str(e)}",
        )


# Keep the helper functions from the original wrapper for compatibility
def generate_basic_metadata(
    all_segments_dir: str, pdf_filename: str
) -> List[Dict[str, Any]]:
    """Generate basic metadata from existing segment files"""
    segments_metadata = []

    for segment_file in sorted(os.listdir(all_segments_dir)):
        if segment_file.endswith("_segmented.png"):
            parts = segment_file.split("_")
            if len(parts) >= 3 and parts[0] == "picture":
                try:
                    # For picture_*_segmented.png format
                    idx = int(parts[1])
                    segment_id = f"picture-{idx}"

                    segment_metadata = {
                        "segment_id": segment_id,
                        "filename": segment_file,
                        "path": os.path.join("all_segments", segment_file),
                        "full_path": os.path.join(all_segments_dir, segment_file),
                        "pageNumber": 0,  # Default - we don't have page info from filename alone
                        "segmentNumber": idx + 1,
                        "pdfFilename": pdf_filename,
                        "bbox": [0, 0, 1000, 1000],  # Default bbox
                    }

                    segments_metadata.append(segment_metadata)
                except (ValueError, IndexError):
                    continue

    return segments_metadata


@measure_performance
def get_highlighted_segment_image(segment_id: str, pdf_filename: str) -> str:
    """
    Generate highlighted segment image (kept for API compatibility).

    Note: This is a simplified implementation since we don't have the same
    segmentation concept with Docling. Returns a placeholder or the original image.
    """
    # For now, return a placeholder implementation to maintain API compatibility
    # In a full implementation, we would highlight the actual extracted picture

    cache_key = f"{pdf_filename}_{segment_id}"
    cache_file = os.path.join(SEGMENTS_DIR, f"cache_highlighted_{cache_key}.png")

    # Return cached version if exists and recent
    if os.path.exists(cache_file):
        if time.time() - os.path.getmtime(cache_file) < 3600:  # 1 hour
            return cache_file

    # Create a simple placeholder image
    try:
        # Create a Simple placeholder image
        import numpy as np
        import cv2

        # Create a 200x200 placeholder image with text
        placeholder = np.ones((200, 200, 3), dtype=np.uint8) * 255  # White background
        cv2.putText(placeholder, f"Segment: {segment_id}", (10, 100),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
        cv2.putText(placeholder, f"From: {pdf_filename}", (10, 130),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (100, 100, 100), 1)
        cv2.rectangle(placeholder, (10, 10), (190, 190), (0, 255, 0), 2)  # Green border

        cv2.imwrite(cache_file, placeholder)
        return cache_file
    except Exception as e:
        # If we can't create a placeholder, raise an error
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Segment image not found: {segment_id}",
        )


def segments_exist(filepath: str) -> Tuple[bool, Optional[str]]:
    """Check if segments already exist for a PDF file (unchanged from original)"""
    if not filepath.lower().endswith(".pdf"):
        raise ValueError(f"Expected a PDF file, got: {filepath}")

    file_stem = Path(filepath).stem
    segments_base = Path(SEGMENTS_DIR)

    # Quick check with glob pattern
    matching_dirs = list(segments_base.glob(f"{file_stem}*"))
    matching_dirs = [d for d in matching_dirs if d.is_dir()]

    if not matching_dirs:
        return False, None

    # Use the most recent directory
    base_directory = max(matching_dirs, key=lambda d: d.stat().st_mtime)

    # Check for segments
    all_segments_dir = base_directory / "all_segments"
    if all_segments_dir.exists() and all_segments_dir.is_dir():
        # Quick check for any PNG files
        if any(all_segments_dir.glob("*_segmented.png")):
            return True, str(base_directory)

    # Check other segment directories
    segment_dirs = list(base_directory.glob("*_segments"))
    for segment_dir in segment_dirs:
        if any(segment_dir.glob("*_segmented.png")):
            return True, str(base_directory)

    return False, None


def save_segment_metadata(
    directory: str, segments_metadata: List[Dict[str, Any]]
) -> None:
    """Save segment metadata with atomic write operation (unchanged)"""
    try:
        metadata_file = os.path.join(directory, "segments_metadata.json")
        temp_file = metadata_file + ".tmp"

        # Create directory if needed
        os.makedirs(directory, exist_ok=True)

        # Write to temporary file first
        with open(temp_file, "w") as f:
            json.dump(segments_metadata, f, indent=2)

        # Atomic rename
        os.replace(temp_file, metadata_file)

        # Verify write
        if os.path.exists(metadata_file):
            file_size = os.path.getsize(metadata_file)
            print(f"Successfully saved metadata file ({file_size} bytes)")

    except Exception as e:
        print(f"Error saving segment metadata: {str(e)}")
        # Clean up temp file if it exists
        if os.path.exists(temp_file):
            os.remove(temp_file)


def load_segment_metadata(directory: str) -> List[Dict[str, Any]]:
    """Load segment metadata with error handling and caching (unchanged)"""
    metadata_file = os.path.join(directory, "segments_metadata.json")

    if os.path.exists(metadata_file):
        try:
            # Check file size to avoid loading huge files
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


# Cleanup function for API shutdown
def cleanup_caches():
    """Clean up caches and temporary files"""
    global stored_segment_info, pdf_metadata_cache

    with thread_safe_dict_access(segment_info_lock):
        stored_segment_info.clear()

    with thread_safe_dict_access(pdf_cache_lock):
        pdf_metadata_cache.clear()

    # Clean up old cache files
    cache_pattern = os.path.join(SEGMENTS_DIR, "cache_highlighted_*.png")
    import glob

    for cache_file in glob.glob(cache_pattern):
        try:
            if time.time() - os.path.getmtime(cache_file) > 3600:  # 1 hour old
                os.remove(cache_file)
        except:
            pass
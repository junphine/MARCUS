import os
import re
import json
import threading
import hashlib
from typing import List, Optional, Tuple, Dict, Any
from pathlib import Path
import cv2
import glob
import numpy as np
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.datamodel.base_models import InputFormat
from docling.document_converter import ConversionResult
from docling_core.types import DoclingDocument
from docling_core.types.doc import PictureItem, TableItem, ListItem, TextItem, SectionHeaderItem
from functools import lru_cache
import time
from contextlib import contextmanager

from app.config import PDF_DIR, SEGMENTS_DIR, MODELS_DIR
from app.modules.ocsr_wrapper import detect_chemical_structure


# Configure Docling to generate picture images
pipeline_options = PdfPipelineOptions(artifacts_path=MODELS_DIR)
pipeline_options.generate_picture_images = True
pipeline_options.do_formula_enrichment = True
pipeline_options.images_scale = 2.0
#pipeline_options.do_chart_extraction = True

doc_converter = DocumentConverter(
    format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
)

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


REF_HEADER_PAT = re.compile(
    r"^\s*(references|bibliography|works cited|参考文献|引用文献)\s*$",
    re.IGNORECASE,
)

def remove_reference_section(doc: DoclingDocument) -> DoclingDocument:
    # 1. 找到参考文献标题在 texts 中的索引
    ref_start_idx = None
    for i, item in enumerate(doc.texts):
        if isinstance(item, SectionHeaderItem) and REF_HEADER_PAT.match(item.text or ""):
            ref_start_idx = i
            break
    if ref_start_idx is None:
        return doc

    # 2. 收集要删除的 item 的 self_ref
    to_remove_refs = set()
    header = doc.texts[ref_start_idx]
    to_remove_refs.add(header.self_ref)
    header_level = getattr(header, "level", 1)

    for item in doc.texts[ref_start_idx + 1:]:
        # 遇到同级或更高级标题则停止
        if isinstance(item, SectionHeaderItem) and getattr(item, "level", 99) <= header_level:
            break
        to_remove_refs.add(item.self_ref)

    # 3. 从 texts 列表中删除
    doc.texts = [t for t in doc.texts if t.self_ref not in to_remove_refs]

    # 4. 从 body.children 中删除对应 RefItem
    doc.body.children = [
        c for c in doc.body.children if c.self_ref not in to_remove_refs
    ]
    return doc


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
    all_segments_dir = os.path.join(base_directory, "images")
    os.makedirs(all_segments_dir, exist_ok=True)

    segments_metadata = []

    try:
        # Use original filename without adding unique IDs
        original_filename = Path(file_path).name

        # Directory layout (shared with the decimer segmentation flow):
        #   uploads/segments/{pdf_stem}/{pdf_stem}.md
        #   uploads/segments/{pdf_stem}/images/picture_N_segmented.png
        # Use the same directory naming as the segmentation router
        seg_safe_name = re.sub(r"[^\w.-]", "_", original_filename)
        pdf_stem = os.path.splitext(seg_safe_name)[0]
        md_path = os.path.join(base_directory, f"{pdf_stem}.md")
        json_path = os.path.join(base_directory, f"{pdf_stem}.json")

        # Convert the document
        conv_result: ConversionResult = doc_converter.convert(file_path)
        doc = conv_result.document

        # remove reference section
        remove_reference_section(doc)

        markdown_text = doc.export_to_markdown()
        # Replace docling's image placeholders with relative image links so the
        # markdown file resolves images relative to its own location
        image_index = 0

        def _image_replacer(_match):
            nonlocal image_index
            image_index += 1
            return (
                f"![Figure {image_index}]"
                f"(images/picture_{image_index}_segmented.png)"
            )

        markdown_text = re.sub(r"<!--\s*image\s*-->", _image_replacer, markdown_text)

        # Save the markdown file next to the images (full document only,
        # so the cache always holds the complete document)
        if md_path:
            with open(md_path, "w", encoding="utf-8") as md_file:
                md_file.write(markdown_text)
        if json_path:
            json_result = doc.export_to_dict()
            with open(json_path, "w", encoding="utf-8") as json_file:
                json.dump(json_result,json_file,ensure_ascii=False,indent=2)

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

                    # Detect whether the picture contains a chemical structure
                    contains_chems = detect_chemical_structure(segment_path)

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
                        "path": os.path.join("images", segment_filename),
                        "pageNumber": page_number,
                        "segmentNumber": picture_counter,
                        "bbox": bbox,  # [x0, y0, x1, y1]
                        "chemical_bboxes": contains_chems,
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

    return base_directory, segments_metadata, markdown_text


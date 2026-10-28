"""OCSR worker — runs inside the dedicated MolScribe/MolDetv2 venv.

Reads one JSON command per line from stdin, writes one JSON line per
response to stdout. Keeps MolDetv2 and MolScribe loaded across calls
so startup cost is paid once per pipeline run, not per molecule.

Commands:
  {"op":"detect","image":"/abs/path.png"}        → {"boxes":[{"xyxy":[x,y,x,y],"conf":0.9}]}


Errors returned as {"error": "..."}.
"""
from __future__ import annotations

import contextlib
import json
import sys
import traceback
from pathlib import Path
import os
import base64
from typing import Optional, Literal
from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from app.schemas.healthcheck import HealthCheck
from app.schemas.error import BadRequestModel, ErrorResponse, NotFoundModel
from app.modules.ocsr_wrapper import process_chemical_structure, save_uploaded_image
from app.modules.depiction import generate_depiction
from app.config import SEGMENTS_DIR, IMAGES_DIR

# Create a router for the OCSR endpoints
router = APIRouter(
    prefix="/detector",
    tags=["ocsr"],
    dependencies=[],
    responses={
        200: {"description": "OK"},
        400: {"description": "Bad Request", "model": BadRequestModel},
        404: {"description": "Not Found", "model": NotFoundModel},
        422: {"description": "Unprocessable Entity", "model": ErrorResponse},
    },
)

_DETECTOR = None
_RECOGNIZER = None


def _get_detector():
    global _DETECTOR
    if _DETECTOR is None:
        # Ultralytics + MolScribe both print startup chatter to stdout.
        # stdout is our JSON channel — redirect any library noise to
        # stderr so the parent only ever reads JSON lines.
        with contextlib.redirect_stdout(sys.stderr):
            from ultralytics import YOLO
            from modelscope import snapshot_download
            ckpt = snapshot_download(
                repo_id="UniParser/MolDetv2",
                allow_file_pattern="moldet_v2_yolo11n_960_doc.pt",
            )
            _DETECTOR = YOLO(ckpt+os.sep+"moldet_v2_yolo11n_960_doc.pt")
    return _DETECTOR


def _get_recognizer():
    global _RECOGNIZER
    if _RECOGNIZER is None:
        with contextlib.redirect_stdout(sys.stderr):
            from molscribe import MolScribe
            from modelscope import snapshot_download
            ckpt = snapshot_download(
                repo_id="polyai/MolScribe",
                allow_file_pattern="swin_base_char_aux_1m.pth",
            )
            _RECOGNIZER = MolScribe(ckpt+os.sep+"swin_base_char_aux_1m.pth", device=None)
    return _RECOGNIZER


@router.get(
    "/boxes",
    summary="Perform predictions on the given image source using the YOLO model.",
    response_description="Return the boxes information",
    status_code=status.HTTP_200_OK,
)
def _detect(image_path: str, imgsz: int = 960, conf: float = 0.3) -> dict:
    det = _get_detector()
    with contextlib.redirect_stdout(sys.stderr):
        results = det.predict(image_path, imgsz=imgsz, conf=conf, verbose=False)
    boxes = []
    for r in results:
        for b in r.boxes:
            xyxy = [float(x) for x in b.xyxy[0].tolist()]
            boxes.append({"xyxy": xyxy, "conf": float(b.conf[0])})
    return {"boxes": boxes}

@router.get(
    "/recognize",
    summary="Perform smiles predictions on the given image source using the YOLO model.",
    response_description="Return the smiles information",
    status_code=status.HTTP_200_OK,
)
def _recognize(image_path: str) -> dict:
    rec = _get_recognizer()
    with contextlib.redirect_stdout(sys.stderr):
        out = rec.predict_image_file(image_path, return_confidence=True)
    return {
        "smiles": out.get("smiles", ""),
        "confidence": float(out.get("confidence", 0.0) or 0.0),
    }

@router.get(
    "/recognize_batch",
    summary="Perform smiles predictions on the given image source using the YOLO model.",
    response_description="Return the smiles information",
    status_code=status.HTTP_200_OK,
)
def _recognize_batch(image_paths: list[str]) -> dict:
    rec = _get_recognizer()
    with contextlib.redirect_stdout(sys.stderr):
        outs = rec.predict_image_files(image_paths, return_confidence=True)
    return {
        "smiles": [o.get("smiles", "") for o in outs],
        "confidence": [float(o.get("confidence", 0.0) or 0.0) for o in outs],
    }



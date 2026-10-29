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
from app.modules.ocsr_wrapper import _get_recognizer,_get_detector
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

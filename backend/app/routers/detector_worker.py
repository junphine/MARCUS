"""OCSR worker — MolDetv2 detection endpoints.

Keeps MolDetv2 loaded across calls so startup cost is paid once per
pipeline run, not per molecule.

Errors returned as {"error": "..."}.
"""
from __future__ import annotations

import contextlib
import sys
from fastapi import APIRouter, status
from app.schemas.error import BadRequestModel, ErrorResponse, NotFoundModel
from app.modules.ocsr_wrapper import _get_detector

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

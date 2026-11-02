"""LightRAG Sidecar writer infrastructure.

Spec: ``docs/LightRAGSidecarFormat-zh.md``.

This package owns the *single executable specification* of the LightRAG Sidecar
file format. Parser engines (native / mineru / docling) hand it an
``IRDoc`` (intermediate representation) describing the document; the writer
emits the spec-compliant ``*.parsed/`` directory.

See :func:`lightrag.sidecar.writer.write_sidecar` for the entry point.
"""

from typing import TYPE_CHECKING

from .ir import (
    AssetSpec,
    IRBlock,
    IRDoc,
    IRDrawing,
    IREquation,
    IRPosition,
    IRTable,
)


__all__ = [
    "AssetSpec",
    "IRBlock",
    "IRDoc",
    "IRDrawing",
    "IREquation",
    "IRPosition",
    "IRTable",
]



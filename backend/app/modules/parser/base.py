"""Unified parser contract for native + external parser engines.

Every engine (native docx, mineru, docling, legacy, plus the internal
``reuse``/``passthrough`` format handlers) implements :class:`BaseParser`.
The pipeline dispatches through the registry
(:mod:`lightrag.parser.registry`) instead of a growing ``if engine == …``
chain.

Design notes:

- ``BaseParser`` carries *behaviour only* (the ``parse`` coroutine and any
  engine-private hooks).  Capability metadata (supported suffixes, queue
  group, endpoint requirements) lives in the registry's lightweight
  ``ParserSpec`` table so capability queries never import a parser
  implementation.
- ``ParseResult.to_dict()`` emits only semantically-present fields so the
  returned dict stays byte-for-byte compatible with the pre-refactor
  ``parse_native``/``parse_mineru``/``parse_docling`` return shapes (the
  worker reads these by ``.get(...)``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
import threading
from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    from ..sidecar.ir import IRDoc  # noqa: F401

@dataclass
class ResolvedSource:
    """The common parse preamble shared by every engine."""

    source_path: Path
    document_name: str
    parsed_dir: Path


@dataclass
class ParseContext:
    """Inputs handed to :meth:`BaseParser.parse`.

    Wraps the LightRAG instance plus the per-document handles so a parser
    does not receive a bare ``self``.  Convenience helpers lazily import
    pipeline-layer functions at call time, keeping this module import-cheap
    and free of import cycles.
    """

    rag: Any
    doc_id: str
    file_path: str
    content_data: dict[str, Any]
    # Set by the active pipeline batch when the user requests cancellation.
    # Native parsers pass it to their synchronous LLM bridge so a worker thread
    # does not remain blocked on a slow title-block judgment.
    pipeline_cancel_event: threading.Event | None = None

    def source_path(self, parser_engine: str) -> Path:
        """Resolve the on-disk source file for this document."""
        from lightrag.pipeline import (
            _call_source_file_resolver,
            _read_source_file,
        )

        return Path(
            _call_source_file_resolver(
                self.rag,
                self.file_path,
                source_file=_read_source_file(self.content_data),
                parser_engine=parser_engine,
            )
        )

    def resolve(self, parser_engine: str) -> ResolvedSource:
        """Resolve ``(source_path, document_name, parsed_dir)``.

        Mirrors the preamble shared by ``parse_mineru``/``parse_docling``/
        ``parse_native``: canonicalize the document name defensively (so
        direct callers may pass absolute or hint-bearing paths) and derive
        the ``__parsed__/<base>.parsed/`` output directory.
        """
        from lightrag.utils_pipeline import (
            normalize_document_file_path,
            parsed_artifact_dir_for,
        )

        source_path = self.source_path(parser_engine)
        document_name = normalize_document_file_path(self.file_path)
        if document_name == "unknown_source":
            document_name = source_path.name or f"{self.doc_id}.bin"
        parsed_dir = parsed_artifact_dir_for(
            document_name, parent_hint=source_path.parent
        )
        return ResolvedSource(source_path, document_name, parsed_dir)

    async def archive_source(self, source_path: str) -> str | None:
        """Archive the source after a successful parse + full_docs sync.

        Resolved through the pipeline module's namespace (where the function
        is imported) so existing tests that patch
        ``lightrag.pipeline.archive_docx_source_after_full_docs_sync`` keep
        intercepting it now that the call site lives in the parser layer.
        """
        import lightrag.pipeline as _pipeline

        return await _pipeline.archive_docx_source_after_full_docs_sync(source_path)


@dataclass
class ParseResult:
    """Structured parser output.

    ``to_dict`` only emits fields that carry meaning so the dict matches the
    pre-refactor return shapes exactly (no spurious ``None``/``False`` keys).
    """

    doc_id: str
    file_path: str
    parse_format: str
    content: str
    blocks_path: str = ""
    parse_engine: str | None = None
    parse_stage_skipped: bool = False
    parse_warnings: dict[str, Any] | None = None
    # LLM cache keys minted during the parse stage (docx smart_heading).
    # Fixed, first-class field: the pipeline mirrors it into
    # doc_status.metadata so adelete_by_doc_id(delete_llm_cache=True) can
    # purge parse-stage cache — there is no chunk llm_cache_list at parse
    # time to carry them.
    smartheading_llm_cache_ids: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "doc_id": self.doc_id,
            "file_path": self.file_path,
            "parse_format": self.parse_format,
            "content": self.content,
            "blocks_path": self.blocks_path,
        }
        if self.parse_engine is not None:
            out["parse_engine"] = self.parse_engine
        if self.parse_stage_skipped:
            out["parse_stage_skipped"] = True
        if self.parse_warnings:
            out["parse_warnings"] = self.parse_warnings
        if self.smartheading_llm_cache_ids:
            out["smartheading_llm_cache_ids"] = self.smartheading_llm_cache_ids
        return out


class BaseParser(ABC):
    """Abstract base for every parser engine.

    Subclasses set ``engine_name`` and implement :meth:`parse`.  Capability
    metadata (suffixes/queue group/endpoint) is declared in the registry
    ``ParserSpec``, not here.
    """

    engine_name: str

    @abstractmethod
    async def parse(self, ctx: ParseContext) -> ParseResult:
        """Parse one document and return its :class:`ParseResult`."""
        ...



class ExternalParserBase(BaseParser):
    """Base for engines that fetch a raw bundle from an external service."""

    raw_dir_suffix: str
    force_reparse_env: str

    # --- engine-private hooks ------------------------------------------------
    @abstractmethod
    def is_bundle_valid(
            self,
            raw_dir: Path,
            source_path: Path,
            *,
            engine_params: Mapping[str, Any] | None = None,
    ) -> bool:
        """Cheap cache-hit check against the raw bundle on disk.

        ``engine_params`` is the per-file engine-parameter override (decoded
        from ``parse_engine``); it MUST participate in the cache signature so an
        overridden document does not spuriously hit a bundle parsed with
        different params.
        """
        ...

    @abstractmethod
    async def download_into(
            self,
            raw_dir: Path,
            source_path: Path,
            *,
            upload_name: str,
            engine_params: Mapping[str, Any] | None = None,
    ) -> None:
        """Fetch the raw bundle into ``raw_dir`` (called on cache miss only).

        ``engine_params`` is the per-file engine-parameter override applied to
        both the request payload and the recorded cache signature.
        """
        ...

    @abstractmethod
    def build_ir(self, raw_dir: Path, document_name: str) -> "IRDoc":
        """Convert the raw bundle to an :class:`IRDoc`."""
        ...

    def validate_ir(self, ir: "IRDoc", *, file_path: str, raw_dir: Path) -> None:
        """Optional post-build validation hook (default no-op)."""

    # --- template ------------------------------------------------------------
    async def parse(self, ctx: ParseContext) -> ParseResult:
        from lightrag.parser.external._common import (
            clear_dir_contents,
            env_bool,
            raw_dir_for_parsed_dir,
        )
        from lightrag.parser.routing import decode_parse_engine, encode_parse_engine
        from lightrag.sidecar import write_sidecar
        from lightrag.utils_pipeline import (
            make_lightrag_doc_content,
            sidecar_uri_for,
        )

        # Per-file engine params are encoded in the stored ``parse_engine``
        # directive (e.g. ``mineru(page_range=1-3)``); decode them once and
        # thread the SAME dict into both the cache-hit check and the download so
        # an overridden doc can never hit a bundle parsed with different params.
        # A malformed/corrupt directive fails this doc loudly rather than
        # silently parsing with no params.
        _engine, engine_params, decode_errs = decode_parse_engine(
            ctx.content_data.get("parse_engine")
            if isinstance(ctx.content_data, dict)
            else None
        )
        if decode_errs:
            raise ValueError(
                f"{self.engine_name}: invalid parse_engine for doc_id={ctx.doc_id}: "
                + "; ".join(decode_errs)
            )
        engine_params = engine_params or None

        rs = ctx.resolve(self.engine_name)
        source = rs.source_path
        if not source.is_file():
            raise FileNotFoundError(
                f"{self.engine_name} source file not found: {source}"
            )
        raw_dir = raw_dir_for_parsed_dir(rs.parsed_dir, suffix=self.raw_dir_suffix)
        force_reparse = env_bool(self.force_reparse_env, False)

        parse_stage_skipped = False
        if not force_reparse and self.is_bundle_valid(
                raw_dir, source, engine_params=engine_params
        ):
            # Cache hit: stay purely local so a re-parse still works when the
            # external endpoint is temporarily unavailable.
            parse_stage_skipped = True
            logger.info("[%s] raw cache hit doc_id=%s", self.engine_name, ctx.doc_id)
        else:
            if force_reparse and raw_dir.exists():
                logger.info(
                    "[%s] %s set; discarding bundle at %s",
                    self.engine_name,
                    self.force_reparse_env,
                    raw_dir,
                )
            # download_into mkdir's raw_dir; we wipe stale contents first so a
            # previous bundle cannot leak into the new one (cache-miss only).
            raw_dir.mkdir(parents=True, exist_ok=True)
            clear_dir_contents(raw_dir)
            logger.info(
                "[%s] Parsing %s %s (may take a few minutes)",
                self.engine_name,
                ctx.doc_id,
                source.name,
            )
            await self.download_into(
                raw_dir,
                source,
                upload_name=rs.document_name,
                engine_params=engine_params,
            )

        ir = self.build_ir(raw_dir, rs.document_name)
        self.validate_ir(ir, file_path=ctx.file_path, raw_dir=raw_dir)
        parsed_data = write_sidecar(
            ir,
            parsed_dir=rs.parsed_dir,
            doc_id=ctx.doc_id,
            engine=self.engine_name,
        )

        await ctx.rag._persist_parsed_full_docs(
            ctx.doc_id,
            {
                "content": make_lightrag_doc_content(parsed_data["content"]),
                "file_path": ctx.file_path,
                "parse_format": FULL_DOCS_FORMAT_LIGHTRAG,
                "sidecar_location": sidecar_uri_for(rs.parsed_dir),
                # Re-encode the engine + params so the persisted directive keeps
                # the per-file params (the `{**existing, **record}` merge in
                # _persist_parsed_full_docs would otherwise revert it to the
                # bare engine name).
                "parse_engine": encode_parse_engine(self.engine_name, engine_params),
                "update_time": int(time.time()),
            },
        )
        await ctx.archive_source(str(source))
        return ParseResult(
            doc_id=ctx.doc_id,
            file_path=ctx.file_path,
            parse_format=FULL_DOCS_FORMAT_LIGHTRAG,
            content=parsed_data["content"],
            blocks_path=parsed_data["blocks_path"],
            parse_engine=self.engine_name,
            parse_stage_skipped=parse_stage_skipped,
        )
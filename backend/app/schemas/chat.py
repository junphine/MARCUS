from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Represents a chat question about the current document.

    Attributes:
        question (str): The user's question.
        pdf_filename (Optional[str]): Filename of the current PDF document
            (as stored on the server). Used to resolve the document's
            working directory for the QwenPaw agent.
        session_id (Optional[str]): Chat session identifier used to keep
            multi-turn conversation context in QwenPaw.
        only_path (bool): When True (default), send only the question and
            let the agent resolve files from the workspace path. When False,
            send the document's markdown content via ``ask_markdown``.
    """

    question: str = Field(..., min_length=1)
    pdf_filename: Optional[str] = None
    session_id: Optional[str] = None
    only_path: bool = True


class ChatResponse(BaseModel):
    """Represents the answer returned for a chat question.

    Attributes:
        answer (str): The generated answer text.
        session_id (Optional[str]): The chat session identifier used.
        document_dir (Optional[str]): The document working directory that
            was handed over to the QwenPaw agent.
    """

    answer: str
    session_id: Optional[str] = None
    document_dir: Optional[str] = None

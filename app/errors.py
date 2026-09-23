"""Application errors with a stable public code, message, and HTTP status.

Every message is a fixed class attribute, never built from an exception, a
provider body, a path, or document content, so nothing sensitive can reach a
client through an error (docs/SPEC.md sections 12 and 13). ``main.py`` renders
these as the SPEC section 12.1 envelope.

This module imports nothing from FastAPI; the status is a plain integer.
"""

from __future__ import annotations


class AppError(Exception):
    """Base class for failures that map to a controlled public response."""

    status_code: int = 500
    code: str = "internal_error"
    message: str = "The request could not be completed."

    def __init__(self) -> None:
        super().__init__(self.code)


class InvalidRequestError(AppError):
    status_code = 422
    code = "invalid_request"
    message = "The request must be multipart/form-data with exactly one 'file' upload."


class UnsupportedFileTypeError(AppError):
    status_code = 415
    code = "unsupported_file_type"
    message = "Supported file types are PDF, Markdown, and plain text."


class UnsupportedMediaTypeError(AppError):
    status_code = 415
    code = "unsupported_media_type"
    message = "The declared media type is missing or does not match the file extension."


class UploadTooLargeError(AppError):
    status_code = 413
    code = "file_too_large"
    message = "The uploaded file exceeds the maximum allowed size."


class UnparseableDocumentError(AppError):
    status_code = 400
    code = "unparseable_document"
    message = "The document could not be parsed as its declared type."


class EmptyDocumentError(AppError):
    status_code = 400
    code = "empty_document"
    message = "The document contains no extractable text."


class EmbeddingProviderError(AppError):
    status_code = 502
    code = "embedding_provider_error"
    message = "The embedding provider is unavailable or returned an invalid response."


class TokenizerUnavailableError(AppError):
    status_code = 503
    code = "tokenizer_unavailable"
    message = "The tokenizer is temporarily unavailable."


class DatabaseUnavailableError(AppError):
    status_code = 503
    code = "database_unavailable"
    message = "The database is unavailable."

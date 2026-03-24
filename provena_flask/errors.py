"""
Standardised error response helpers — T-056.

All API error responses follow the shape:
  {
    "error": {
      "code": "SNAKE_CASE_CODE",
      "message": "Human-readable explanation",
      "request_id": "<uuid>"
    }
  }
"""
from __future__ import annotations

import uuid
from flask import jsonify, request


def _request_id() -> str:
    """Return the X-Request-ID from the current request, or generate a new one."""
    return request.headers.get("X-Request-ID", str(uuid.uuid4()))


def error_response(code: str, message: str, http_status: int):
    """
    Build a standardised JSON error response.

    Args:
        code:        Snake-case error code (e.g. 'INVALID_IMAGE').
        message:     Human-readable description.
        http_status: HTTP status code to return.

    Returns:
        Flask (Response, int) tuple.
    """
    req_id = _request_id()
    resp = jsonify(
        {
            "error": {
                "code": code,
                "message": message,
                "request_id": req_id,
            }
        }
    )
    resp.headers["X-Request-ID"] = req_id
    return resp, http_status


# Convenience shorthands
def bad_request(message: str = "Bad request", code: str = "BAD_REQUEST"):
    return error_response(code, message, 400)


def unauthorized(message: str = "Unauthorized", code: str = "UNAUTHORIZED"):
    return error_response(code, message, 401)


def not_found(message: str = "Not found", code: str = "NOT_FOUND"):
    return error_response(code, message, 404)


def internal_error(message: str = "Internal server error"):
    return error_response("INTERNAL_ERROR", message, 500)

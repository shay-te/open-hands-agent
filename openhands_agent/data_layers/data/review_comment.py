from __future__ import annotations

from pydantic import BaseModel


class ReviewComment(BaseModel):
    pull_request_id: str
    comment_id: str
    author: str
    body: str

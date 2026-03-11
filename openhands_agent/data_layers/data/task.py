from __future__ import annotations

from pydantic import BaseModel


class Task(BaseModel):
    id: str
    summary: str
    description: str
    branch_name: str

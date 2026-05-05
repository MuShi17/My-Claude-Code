"""Pydantic models for the web API."""

from __future__ import annotations

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str


class ConfirmRequest(BaseModel):
    id: str
    approved: bool


class MemoryCreate(BaseModel):
    name: str
    description: str = ""
    type: str = "project"
    content: str


class SessionItem(BaseModel):
    id: str
    model: str
    cwd: str
    start_time: str
    message_count: int
    valid: bool = True


class MemoryItem(BaseModel):
    name: str
    description: str
    type: str
    filename: str


class SkillItem(BaseModel):
    name: str
    description: str
    source: str
    user_invocable: bool
    context: str


class AgentStatus(BaseModel):
    ready: bool
    model: str
    permission_mode: str
    session_id: str


class CostInfo(BaseModel):
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    max_cost_usd: float | None = None
    turns: int = 0
    max_turns: int | None = None

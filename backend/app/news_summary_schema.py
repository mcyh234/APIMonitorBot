"""Strict, source-referenced summary documents and bounded operator settings."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SummarySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    every_posts: int = Field(default=10, ge=1, le=100)
    max_wait_minutes: int = Field(default=0, ge=0, le=1440)
    detail: Literal["brief", "standard", "detailed"] = "standard"
    timeline_enabled: bool = True
    connections_enabled: bool = True
    memory_enabled: bool = True
    memory_days: int = Field(default=30, ge=1, le=365)
    memory_items: int = Field(default=300, ge=10, le=1000)
    context_items: int = Field(default=12, ge=1, le=30)
    focus: str = Field(default="", max_length=500)


class StrictDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TimelineNode(StrictDocument):
    text: str = Field(min_length=1, max_length=260)
    source_ids: list[str] = Field(min_length=1, max_length=4)


class EventTimeline(StrictDocument):
    title: str = Field(min_length=1, max_length=100)
    nodes: list[TimelineNode] = Field(min_length=1, max_length=10)


class Connection(StrictDocument):
    current_ids: list[str] = Field(min_length=1, max_length=4)
    history_ids: list[str] = Field(min_length=1, max_length=4)
    relation: Literal["进展", "补充", "对比", "可能相关"]
    explanation: str = Field(min_length=1, max_length=260)


class SummaryDocument(StrictDocument):
    title: str = Field(min_length=1, max_length=100)
    markdown: str = Field(min_length=1, max_length=6000)
    covered_ids: list[str] = Field(min_length=1, max_length=100)
    timelines: list[EventTimeline] = Field(default_factory=list, max_length=4)
    connections: list[Connection] = Field(default_factory=list, max_length=8)

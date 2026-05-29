"""Pydantic data models for the News Intelligence pipeline."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# --- Enums ---

class AssetType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    ARTICLE = "article"
    MAP = "map"
    SATELLITE = "satellite"
    ANALYSIS = "analysis"


class LicenseTier(str, Enum):
    PUBLIC_DOMAIN = "public_domain"
    CC_BY = "cc_by"
    EDITORIAL = "editorial"
    UNKNOWN = "unknown"


class CrawlScope(str, Enum):
    """What to crawl for a given keyword."""
    FULL = "full"              # Everything — new keyword
    NEWS_AND_SOCIAL = "news_social"  # Skip stock, crawl news + social
    NEWS_ONLY = "news_only"    # Only fresh news articles


# --- Mining models (Script 1 & 2) ---

class TranscriptResult(BaseModel):
    video_url: str
    title: str = ""
    description: str = ""
    transcript_text: str = ""
    language: str = ""
    duration_seconds: float = 0.0
    extracted_at: datetime = Field(default_factory=datetime.now)


class ExtractedEntity(BaseModel):
    name: str
    name_en: str = ""
    category: str  # weapons, locations, events, context
    relevance: float = 1.0


class KeywordSet(BaseModel):
    """Output of Script 2 — entities grouped by category."""
    source_video: str = ""
    extracted_at: datetime = Field(default_factory=datetime.now)
    weapons: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)
    context: list[str] = Field(default_factory=list)
    all_entities: list[ExtractedEntity] = Field(default_factory=list)

    @property
    def all_keywords(self) -> list[str]:
        return self.weapons + self.locations + self.events + self.context


# --- Crawl Planning (Script 2.5) ---

class KeywordCrawlPlan(BaseModel):
    keyword: str
    scope: CrawlScope = CrawlScope.FULL
    skip_sources: list[str] = Field(default_factory=list)
    reason: str = ""


class CrawlPlan(BaseModel):
    created_at: datetime = Field(default_factory=datetime.now)
    keywords: list[KeywordCrawlPlan] = Field(default_factory=list)


# --- Discovery & Assets (Script 3 & 4) ---

class DiscoveredURL(BaseModel):
    url: str
    source: str
    keyword: str
    title: str = ""
    asset_type: AssetType = AssetType.IMAGE
    resolution: str = ""
    metadata: dict = Field(default_factory=dict)


class Asset(BaseModel):
    filename: str
    source: str
    source_url: str = ""
    keyword: str
    asset_type: AssetType
    license_tier: LicenseTier = LicenseTier.UNKNOWN
    usable_in_monetized_video: bool = False
    quality_score: int = 0
    resolution: str = ""
    phash: str = ""
    downloaded_at: datetime = Field(default_factory=datetime.now)
    file_size_bytes: int = 0
    note: str = ""


class AssetManifest(BaseModel):
    keyword: str
    last_updated: datetime = Field(default_factory=datetime.now)
    total_assets: int = 0
    assets: list[Asset] = Field(default_factory=list)


# --- Failed URL tracking ---

class FailedURL(BaseModel):
    url: str
    source: str
    keyword: str
    error: str
    phase: str = ""
    attempts: int = 1
    last_attempt: datetime = Field(default_factory=datetime.now)

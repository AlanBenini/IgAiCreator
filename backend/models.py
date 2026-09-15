from typing import List, Optional
from pydantic import BaseModel

class PostRecord(BaseModel):
    id: str
    media_type: str
    media_url: str
    date: str
    caption: str
    likes: int
    comments: int
    reach: int
    saved: int
    interactions: int
    engagement_rate: float
    save_rate: float
    conversation_rate: float
    strengths: str
    weaknesses: str
    local_files: List[str] = []
    permalink: Optional[str] = None

class AnalyticsSummary(BaseModel):
    total_posts: int
    avg_engagement_rate: float
    avg_save_rate: float
    avg_conversation_rate: float
    total_reach: int
    top_post_id: str
    posts: List[PostRecord]

class AssetRecord(BaseModel):
    id: str
    filename: str
    mime_type: str
    size: int
    url: str

class Opportunity(BaseModel):
    id: str
    title: str
    start: str
    priority: str
    reason: str
    capture_brief: List[str]

class CopyItem(BaseModel):
    number: int
    hook: str
    caption: str
    hashtags: List[str] = []

class CopyGenerationResponse(BaseModel):
    assets: List[AssetRecord]
    copies: List[CopyItem]
    draft: Optional["ContentDraft"] = None

class ContentDraft(BaseModel):
    id: str
    title: str
    content_format: str
    status: str
    asset_ids: List[str]
    copies: List[CopyItem]
    created_at: str

class ContentStatusRequest(BaseModel):
    status: str

class ContentPublishResponse(BaseModel):
    id: str
    status: str
    platform_id: Optional[str] = None

class CalendarEventRequest(BaseModel):
    title: str
    description: str = ""
    start: str
    end: str

class CalendarEventResponse(BaseModel):
    id: str
    html_link: Optional[str] = None

class GoogleTokenRequest(BaseModel):
    access_token: str
    refresh_token: str = ""

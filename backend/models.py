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

class CalendarScheduleRequest(BaseModel):
    title: str
    description: str = ""
    start: str
    end: str
    frequency: str = "weekly"
    days: List[str] = []
    weeks: int = 1

class CalendarEventResponse(BaseModel):
    id: str
    html_link: Optional[str] = None

class CalendarBulkDeleteRequest(BaseModel):
    event_ids: List[str]

class CalendarUpdateRequest(BaseModel):
    title: str
    description: str = ""
    start: str
    end: str

class GoogleTokenRequest(BaseModel):
    access_token: str
    refresh_token: str = ""


class MeetingSuggestion(BaseModel):
    """Riunioni ripetute nello stesso giorno in settimane diverse: possibile piano editoriale."""
    weekday: str
    weekday_label: str
    hour: int
    occurrences: int
    weeks: List[str] = []
    titles: List[str] = []
    message: str


class EditorialPlanRequest(BaseModel):
    topic: str
    content_format: str = "instagram_post"
    start_date: str
    weeks: int = 4
    frequency: str = "weekly"
    post_days: List[str] = []
    post_time: str = "10:00"
    post_duration_minutes: int = 30
    meeting_shift_days: int = 0
    meeting_time: str = "09:00"
    meeting_duration_minutes: int = 60
    notes: str = ""


class EditorialPlan(BaseModel):
    id: str
    topic: str
    content_format: str
    start_date: str
    weeks: int
    frequency: str
    post_days: List[str]
    post_time: str
    post_duration_minutes: int
    meeting_days: List[str]
    meeting_time: str
    meeting_duration_minutes: int
    notes: str = ""
    posts_planned: int
    meetings_planned: int
    post_event_id: Optional[str] = None
    post_event_link: Optional[str] = None
    meeting_event_id: Optional[str] = None
    meeting_event_link: Optional[str] = None
    created_at: str


class ScheduledPost(BaseModel):
    id: str
    user_id: Optional[str] = None
    caption: str
    asset_ids: List[str]
    scheduled_at: str
    status: str = "SCHEDULED"
    calendar_event_id: Optional[str] = None
    calendar_event_title: Optional[str] = None
    created_at: str
    published_at: Optional[str] = None
    platform_id: Optional[str] = None
    error: Optional[str] = None


class PublicationRunResult(BaseModel):
    due: int
    published: int
    failed: int
    results: List[ScheduledPost] = []

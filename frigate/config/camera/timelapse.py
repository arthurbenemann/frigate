from enum import Enum
from typing import Optional

from pydantic import Field

from ..base import FrigateBaseModel
from .record import RecordQualityEnum

__all__ = [
    "TimelapseConfig",
    "TimelapseCodecEnum",
]


class TimelapseCodecEnum(str, Enum):
    h264 = "h264"
    h265 = "h265"


class TimelapseConfig(FrigateBaseModel):
    enabled: bool = Field(
        default=False,
        title="Enable timelapse capture",
        description="Capture a single frame at a fixed interval and build storage-efficient long-term timelapse segments.",
    )
    interval: int = Field(
        default=60,
        ge=1,
        title="Capture interval",
        description="Seconds of real time between captured frames. This is the capture cadence and sets the finest detail available; the playback speed and frame rate are chosen when generating a video.",
    )
    height: Optional[int] = Field(
        default=None,
        ge=120,
        title="Frame height",
        description="Height in pixels to scale captured frames to; width follows the stream aspect ratio. Leave empty to keep the full resolution of the highest-resolution stream.",
    )
    quality: RecordQualityEnum = Field(
        default=RecordQualityEnum.medium,
        title="Timelapse quality",
        description="Encoding quality for timelapse segments (very_low, low, medium, high, very_high).",
    )
    codec: TimelapseCodecEnum = Field(
        default=TimelapseCodecEnum.h265,
        title="Timelapse codec",
        description="Codec used for timelapse segments. h265 produces smaller files; h264 has wider playback compatibility.",
    )
    retain_days: float = Field(
        default=365,
        ge=0,
        title="Retention days",
        description="Number of days to retain timelapse segments. Independent of recording retention.",
    )

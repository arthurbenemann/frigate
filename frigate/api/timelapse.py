"""Timelapse apis."""

import logging
import random
import string
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from frigate.api.auth import require_camera_access
from frigate.api.defs.tags import Tags
from frigate.models import TimelapseSegment
from frigate.record.timelapse import TimelapseAssembler

logger = logging.getLogger(__name__)

router = APIRouter(tags=[Tags.timelapse])


def _generate_export_id(camera_name: str) -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{camera_name}_{suffix}"


@router.get(
    "/timelapse/{camera_name}/segments",
    dependencies=[Depends(require_camera_access)],
    summary="List timelapse segments",
    description="List stored timelapse segments for a camera within a time range.",
)
def get_timelapse_segments(
    camera_name: str,
    start_ts: Optional[float] = None,
    end_ts: Optional[float] = None,
):
    query = TimelapseSegment.select().where(TimelapseSegment.camera == camera_name)

    if start_ts is not None:
        query = query.where(TimelapseSegment.end_time >= start_ts)

    if end_ts is not None:
        query = query.where(TimelapseSegment.start_time <= end_ts)

    segments = query.order_by(TimelapseSegment.start_time.asc()).dicts().iterator()
    return JSONResponse(content=[s for s in segments])


@router.post(
    "/timelapse/{camera_name}/generate/start/{start_ts}/end/{end_ts}",
    dependencies=[Depends(require_camera_access)],
    summary="Generate a timelapse video",
    description="Assemble stored timelapse segments for a date range into a single video export.",
)
def generate_timelapse(
    request: Request,
    camera_name: str,
    start_ts: float,
    end_ts: float,
    name: Optional[str] = None,
    speed: float = 3600,
    fps: int = 30,
):
    config = request.app.frigate_config

    if camera_name not in config.cameras:
        return JSONResponse(
            content={
                "success": False,
                "message": f"{camera_name} is not a valid camera.",
            },
            status_code=404,
        )

    if end_ts <= start_ts:
        return JSONResponse(
            content={"success": False, "message": "End time must be after start time."},
            status_code=400,
        )

    if speed <= 0 or fps < 1:
        return JSONResponse(
            content={
                "success": False,
                "message": "Speed must be positive and fps at least 1.",
            },
            status_code=400,
        )

    has_segments = (
        TimelapseSegment.select()
        .where(
            (TimelapseSegment.camera == camera_name)
            & (TimelapseSegment.start_time < end_ts)
            & (TimelapseSegment.end_time > start_ts)
        )
        .exists()
    )

    if not has_segments:
        return JSONResponse(
            content={
                "success": False,
                "message": "No timelapse segments found for the requested range.",
            },
            status_code=404,
        )

    export_id = _generate_export_id(camera_name)
    TimelapseAssembler(
        config, export_id, camera_name, name, start_ts, end_ts, speed, fps
    ).start()

    return JSONResponse(
        content={
            "success": True,
            "message": "Timelapse generation started.",
            "export_id": export_id,
        },
        status_code=202,
    )

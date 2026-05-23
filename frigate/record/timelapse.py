"""Capture interval frames from the highest-resolution stream and build
storage-efficient long-term timelapse segments, plus assemble them on demand."""

import datetime
import logging
import os
import subprocess as sp
import threading
from multiprocessing.synchronize import Event as MpEvent
from pathlib import Path

from frigate.config import CameraConfig, FrigateConfig, RecordQualityEnum
from frigate.config.camera.timelapse import TimelapseCodecEnum
from frigate.const import (
    CLIPS_DIR,
    EXPORT_DIR,
    FFMPEG_HVC1_ARGS,
    TIMELAPSE_DIR,
    TIMELAPSE_SEGMENT_FPS,
)
from frigate.models import Export, TimelapseSegment

logger = logging.getLogger(__name__)

TIMELAPSE_FRAME_TYPE = "webp"

# libwebp quality for the captured still frames (kept high since these are the
# source for the encoded segment and are deleted afterwards)
TIMELAPSE_QUALITY_WEBP = {
    RecordQualityEnum.very_low: 70,
    RecordQualityEnum.low: 80,
    RecordQualityEnum.medium: 85,
    RecordQualityEnum.high: 90,
    RecordQualityEnum.very_high: 95,
}
# libx264/libx265 CRF per configured level (lower is higher quality / larger file)
TIMELAPSE_QUALITY_CRF = {
    RecordQualityEnum.very_low: 32,
    RecordQualityEnum.low: 30,
    RecordQualityEnum.medium: 27,
    RecordQualityEnum.high: 23,
    RecordQualityEnum.very_high: 20,
}

# bound a single-frame grab so a stalled camera can't block the capture loop
CAPTURE_TIMEOUT = 30


def get_timelapse_camera_dir(camera: str) -> str:
    return os.path.join(TIMELAPSE_DIR, camera)


def get_timelapse_frames_dir(camera: str) -> str:
    return os.path.join(get_timelapse_camera_dir(camera), "frames")


def get_frame_cache_name(camera: str, frame_time: float) -> str:
    return os.path.join(
        get_timelapse_frames_dir(camera),
        f"{frame_time}.{TIMELAPSE_FRAME_TYPE}",
    )


def get_segment_end(frame_time: float) -> float:
    """Return the next UTC midnight, when the current segment should be closed."""
    dt = datetime.datetime.fromtimestamp(frame_time, tz=datetime.timezone.utc)
    return (
        (dt + datetime.timedelta(days=1))
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .timestamp()
    )


def get_speedup_factor(speed: float, interval: int) -> float:
    """Return the setpts multiplier to retime a stored segment to ``speed``.

    Stored segments play at ``interval * TIMELAPSE_SEGMENT_FPS`` real seconds
    per playback second, so scaling by ``speed / that`` yields the requested
    speed (real seconds shown per second of output video).
    """
    return speed / (interval * TIMELAPSE_SEGMENT_FPS)


def get_highres_input(config: CameraConfig) -> tuple[str, list[str]]:
    """Return the highest-resolution input URL and connection args for a camera.

    Prefers the ``record`` role (typically full resolution), falling back to the
    ``detect`` input. RTSP sources are forced over TCP for a reliable one-shot
    grab.
    """
    inputs = config.ffmpeg.inputs
    selected = next(
        (i for i in inputs if "record" in i.roles),
        next((i for i in inputs if "detect" in i.roles), inputs[0]),
    )
    path = str(selected.path)
    input_args = ["-rtsp_transport", "tcp"] if path.startswith("rtsp") else []
    return path, input_args


class TimelapseSegmentConverter(threading.Thread):
    """Compress collected interval frames into a single temporally-compressed mp4."""

    def __init__(self, config: CameraConfig, frame_times: list[float]) -> None:
        super().__init__(name=f"{config.name}_timelapse_converter")
        self.config = config
        self.camera_name = config.name or ""
        self.frame_times = frame_times
        self.first_ts = frame_times[0]
        self.last_ts = frame_times[-1]
        self.path = os.path.join(
            get_timelapse_camera_dir(self.camera_name),
            f"{self.first_ts}-{self.last_ts}.mp4",
        )

    def _build_command(self) -> list[str]:
        tl = self.config.timelapse

        if tl.codec == TimelapseCodecEnum.h265:
            codec_args = ["-c:v", "libx265", *FFMPEG_HVC1_ARGS]
        else:
            codec_args = ["-c:v", "libx264"]

        return [
            self.config.ffmpeg.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-f",
            "concat",
            "-y",
            "-protocol_whitelist",
            "pipe,file",
            "-safe",
            "0",
            "-threads",
            "1",
            "-i",
            "/dev/stdin",
            "-an",
            "-threads",
            "1",
            *codec_args,
            "-preset",
            "medium",
            "-crf",
            str(TIMELAPSE_QUALITY_CRF[tl.quality]),
            "-r",
            str(TIMELAPSE_SEGMENT_FPS),
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            self.path,
        ]

    def run(self) -> None:
        frame_duration = 1 / TIMELAPSE_SEGMENT_FPS
        playlist: list[str] = []

        for idx, frame_time in enumerate(self.frame_times):
            playlist.append(
                f"file '{get_frame_cache_name(self.camera_name, frame_time)}'"
            )
            if idx != len(self.frame_times) - 1:
                playlist.append(f"duration {frame_duration}")

        p = sp.run(
            self._build_command(),
            input="\n".join(playlist),
            encoding="ascii",
            capture_output=True,
        )

        if p.returncode != 0:
            logger.error(
                "Error saving timelapse segment for %s :: %s",
                self.camera_name,
                p.stderr,
            )
            # leave frames in place so the next attempt can retry the segment
            return

        logger.debug("Saved timelapse segment for %s", self.camera_name)
        TimelapseSegment.insert(
            {
                TimelapseSegment.id.name: f"{self.camera_name}_{self.last_ts}",
                TimelapseSegment.camera.name: self.camera_name,
                TimelapseSegment.path.name: self.path,
                TimelapseSegment.start_time.name: self.first_ts,
                TimelapseSegment.end_time.name: self.last_ts,
                TimelapseSegment.duration.name: self.last_ts - self.first_ts,
            }
        ).execute()

        for frame_time in self.frame_times:
            Path(get_frame_cache_name(self.camera_name, frame_time)).unlink(
                missing_ok=True
            )


class TimelapseRecorder:
    """Per-camera capture state: grab a full-res frame each interval and roll
    the collected frames into a segment at each daily boundary."""

    def __init__(self, config: CameraConfig) -> None:
        self.config = config
        self.camera_name = config.name or ""
        self.last_capture: float = 0
        self.segment_end: float = 0
        self.output_frames: list[float] = []
        self.capture_interval = config.timelapse.interval

        Path(get_timelapse_frames_dir(self.camera_name)).mkdir(
            parents=True, exist_ok=True
        )
        self._resume_from_cache()

    def _resume_from_cache(self) -> None:
        """Resume an in-progress segment after a restart."""
        frames_dir = get_timelapse_frames_dir(self.camera_name)
        cached: list[float] = []

        for entry in os.listdir(frames_dir):
            if not entry.endswith(f".{TIMELAPSE_FRAME_TYPE}"):
                continue
            try:
                cached.append(float(entry[: -(len(TIMELAPSE_FRAME_TYPE) + 1)]))
            except ValueError:
                continue

        if not cached:
            return

        cached.sort()
        self.output_frames = cached
        self.last_capture = cached[-1]
        self.segment_end = get_segment_end(cached[0])

    def _capture_frame(self, frame_time: float) -> bool:
        """Grab a single full-resolution frame from the highest-res stream."""
        url, input_args = get_highres_input(self.config)
        cache_path = get_frame_cache_name(self.camera_name, frame_time)
        tl = self.config.timelapse

        cmd = [
            self.config.ffmpeg.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-y",
            *input_args,
            "-i",
            url,
            "-frames:v",
            "1",
        ]

        if tl.height is not None:
            cmd.extend(["-vf", f"scale=-2:{tl.height}"])

        cmd.extend(
            [
                "-c:v",
                "libwebp",
                "-quality",
                str(TIMELAPSE_QUALITY_WEBP[tl.quality]),
                cache_path,
            ]
        )

        try:
            p = sp.run(cmd, capture_output=True, timeout=CAPTURE_TIMEOUT)
        except sp.TimeoutExpired:
            logger.warning(
                "Timed out capturing timelapse frame for %s", self.camera_name
            )
            return False

        if p.returncode != 0 or not os.path.exists(cache_path):
            logger.warning(
                "Failed to capture timelapse frame for %s :: %s",
                self.camera_name,
                p.stderr.decode() if p.stderr else "ffmpeg failed",
            )
            return False

        return True

    def _close_segment(self) -> None:
        if len(self.output_frames) > 1:
            TimelapseSegmentConverter(self.config, self.output_frames).start()
        else:
            for frame_time in self.output_frames:
                Path(get_frame_cache_name(self.camera_name, frame_time)).unlink(
                    missing_ok=True
                )

        self.output_frames = []

    def tick(self, now: float) -> None:
        """Called periodically; capture a frame and roll segments as needed."""
        if not self.config.timelapse.enabled:
            return

        if self.segment_end and now >= self.segment_end:
            self._close_segment()
            self.segment_end = 0

        if self.last_capture and now - self.last_capture < self.capture_interval:
            return

        if not self._capture_frame(now):
            return

        if self.segment_end == 0:
            self.segment_end = get_segment_end(now)

        self.last_capture = now
        self.output_frames.append(now)


class TimelapseManager(threading.Thread):
    """Drive periodic timelapse capture for all cameras from one thread."""

    def __init__(self, config: FrigateConfig, stop_event: MpEvent) -> None:
        super().__init__(name="timelapse_manager")
        self.config = config
        self.stop_event = stop_event
        self.recorders: dict[str, TimelapseRecorder] = {}

    def run(self) -> None:
        if self.config.safe_mode:
            logger.info("Safe mode enabled, skipping timelapse capture")
            return

        while not self.stop_event.wait(1):
            now = datetime.datetime.now().timestamp()

            for camera, cam_config in self.config.cameras.items():
                if not cam_config.timelapse.enabled:
                    continue

                recorder = self.recorders.get(camera)
                if recorder is None:
                    recorder = TimelapseRecorder(cam_config)
                    self.recorders[camera] = recorder

                try:
                    recorder.tick(now)
                except Exception:
                    logger.exception("Error capturing timelapse for %s", camera)

        logger.info("Exiting timelapse manager...")


class TimelapseAssembler(threading.Thread):
    """Re-encode the timelapse segments for a date range into one mp4 export.

    The capture interval is fixed when frames are stored, but the playback
    speed and frame rate are chosen here (per generation), so the segments are
    retimed with ``setpts`` rather than stream-copied. The result is registered
    as a standard ``Export`` so it appears in the existing exports UI.
    """

    def __init__(
        self,
        config: FrigateConfig,
        export_id: str,
        camera: str,
        name: str | None,
        start_time: float,
        end_time: float,
        speed: float,
        fps: int,
    ) -> None:
        super().__init__(name=f"{camera}_timelapse_assembler")
        self.config = config
        self.export_id = export_id
        self.camera = camera
        self.requested_name = name
        self.start_time = start_time
        self.end_time = end_time
        self.speed = speed
        self.fps = fps

    def _segments(self) -> list[TimelapseSegment]:
        return list(
            TimelapseSegment.select()
            .where(
                (TimelapseSegment.camera == self.camera)
                & (TimelapseSegment.start_time < self.end_time)
                & (TimelapseSegment.end_time > self.start_time)
            )
            .order_by(TimelapseSegment.start_time.asc())
        )

    def _save_thumbnail(self, first_segment: str, thumb_path: str) -> bool:
        result = sp.run(
            [
                self.config.ffmpeg.ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "warning",
                "-y",
                "-i",
                first_segment,
                "-frames:v",
                "1",
                thumb_path,
            ],
            capture_output=True,
        )
        return result.returncode == 0

    def run(self) -> None:
        segments = self._segments()

        if not segments:
            logger.warning(
                "No timelapse segments found for %s in requested range", self.camera
            )
            return

        os.makedirs(EXPORT_DIR, exist_ok=True)
        os.makedirs(os.path.join(CLIPS_DIR, "export"), exist_ok=True)

        filename_start = datetime.datetime.fromtimestamp(self.start_time).strftime(
            "%Y%m%d_%H%M%S"
        )
        filename_end = datetime.datetime.fromtimestamp(self.end_time).strftime(
            "%Y%m%d_%H%M%S"
        )
        cleaned_id = self.export_id.split("_")[-1]
        video_path = (
            f"{EXPORT_DIR}/{self.camera}_timelapse_"
            f"{filename_start}-{filename_end}_{cleaned_id}.mp4"
        )
        thumb_path = os.path.join(CLIPS_DIR, f"export/{self.export_id}.webp")

        export_name = (
            self.requested_name
            or f"{self.camera.replace('_', ' ')} timelapse {filename_start}-{filename_end}"
        )

        Export.insert(
            {
                Export.id: self.export_id,
                Export.camera: self.camera,
                Export.name: export_name,
                Export.date: self.start_time,
                Export.video_path: video_path,
                Export.thumb_path: thumb_path,
                Export.in_progress: True,
            }
        ).execute()

        playlist = "\n".join(f"file '{segment.path}'" for segment in segments)

        cam_timelapse = self.config.cameras[self.camera].timelapse
        factor = get_speedup_factor(self.speed, cam_timelapse.interval)

        if cam_timelapse.codec == TimelapseCodecEnum.h265:
            codec_args = ["-c:v", "libx265", *FFMPEG_HVC1_ARGS]
        else:
            codec_args = ["-c:v", "libx264"]

        result = sp.run(
            [
                self.config.ffmpeg.ffmpeg_path,
                "-hide_banner",
                "-loglevel",
                "warning",
                "-f",
                "concat",
                "-safe",
                "0",
                "-protocol_whitelist",
                "pipe,file",
                "-i",
                "/dev/stdin",
                "-an",
                "-vf",
                f"setpts=PTS/{factor:.6f}",
                "-r",
                str(self.fps),
                *codec_args,
                "-preset",
                "medium",
                "-crf",
                str(TIMELAPSE_QUALITY_CRF[cam_timelapse.quality]),
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                video_path,
            ],
            input=playlist,
            encoding="ascii",
            capture_output=True,
        )

        if result.returncode != 0:
            logger.error(
                "Failed to assemble timelapse for %s :: %s",
                self.camera,
                result.stderr,
            )
            Path(video_path).unlink(missing_ok=True)
            Export.delete().where(Export.id == self.export_id).execute()
            return

        if not self._save_thumbnail(str(segments[0].path), thumb_path):
            logger.warning("Failed to create timelapse thumbnail for %s", self.camera)

        Export.update({Export.in_progress: False}).where(
            Export.id == self.export_id
        ).execute()
        logger.debug("Finished assembling timelapse %s", video_path)

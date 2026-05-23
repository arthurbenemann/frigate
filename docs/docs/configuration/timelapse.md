---
id: timelapse
title: Timelapse
---

Frigate can capture a single frame at a fixed interval and compress those frames into
storage-efficient long-term timelapse segments. This is designed for very long
timelapses (for example, a garden over a whole year) without the cost of retaining
continuous recordings.

## How it works

Unlike exporting a sped-up copy of continuous recordings (which requires keeping the
full-rate video on disk), interval timelapse works in three stages:

1. **Capture** — every `interval` seconds a single full-resolution frame is grabbed
   from the camera's highest-resolution stream (the `record`-role input, falling back to
   `detect`) with a short ffmpeg one-shot, optionally scaled to `height`.
2. **Compress** — once a day the captured frames are
   encoded into a single temporally-compressed `mp4` and the intermediate frames are
   deleted. Because a mostly-static scene compresses extremely well, a full year is on
   the order of a few GB per camera rather than terabytes.
3. **Generate** — on demand, the stored segments for a date range are retimed to a
   chosen speed and frame rate into a single video that appears in the **Exports** page.

Segments are stored under `/media/frigate/timelapse/<camera>/`.

## Configuration

Timelapse capture is disabled by default. Enable it globally or per camera:

The config only controls **capture and storage**. The playback speed and frame rate are
chosen later, when generating a video.

```yaml
timelapse:
  enabled: true
  # seconds of real time between captured frames (capture cadence). Smaller
  # values capture more detail but use more storage.
  interval: 60
  # output frame height in pixels (width follows the camera aspect ratio);
  # defaults to the camera's detect height when omitted
  height: 720
  # very_low, low, medium, high, very_high
  quality: medium
  # h265 (smaller files) or h264 (wider playback compatibility)
  codec: h265
  # how long to keep segments, independent of recording retention
  retain_days: 365

cameras:
  garden:
    timelapse:
      enabled: true
      interval: 120
```

## Generating a timelapse video

In the Web UI, open the **Exports** page and use **Generate Timelapse**. Choose the
camera, a start and end date, the playback **speed** (real seconds shown per second of
video) and **frame rate**, and an optional name. Frigate retimes the stored segments to
those settings into a single video that appears alongside other exports.

Because the capture interval is the finest detail stored, a generated video can play at
that detail or faster, but not slower/more-detailed than the capture interval allows.

This can also be triggered via the API:

```
POST /api/timelapse/<camera>/generate/start/<start_ts>/end/<end_ts>?speed=3600&fps=30
```

The list of available segments for a camera and range is available at:

```
GET /api/timelapse/<camera>/segments?start_ts=<start_ts>&end_ts=<end_ts>
```

## Notes

- Each capture opens a brief connection to the camera's highest-resolution stream. At
  long intervals this is negligible, but very short intervals mean more frequent grabs.
- Generating a video re-encodes the stored segments to apply the chosen speed and frame
  rate, so generation takes longer for very large date ranges.
- Retention is independent of recording retention, which is what makes long-term
  timelapses practical without keeping continuous recordings.

import datetime
import unittest

from frigate.config import FrigateConfig
from frigate.config.camera.timelapse import TimelapseCodecEnum
from frigate.record.timelapse import (
    get_highres_input,
    get_segment_end,
    get_speedup_factor,
)


class TestTimelapseConfig(unittest.TestCase):
    def setUp(self):
        self.minimal = {
            "mqtt": {"host": "mqtt"},
            "cameras": {
                "back": {
                    "ffmpeg": {
                        "inputs": [
                            {"path": "rtsp://10.0.0.1:554/video", "roles": ["detect"]}
                        ]
                    },
                    "detect": {"height": 1080, "width": 1920, "fps": 5},
                }
            },
        }

    def test_timelapse_defaults(self):
        config = FrigateConfig(**self.minimal)
        timelapse = config.cameras["back"].timelapse
        assert timelapse.enabled is False
        assert timelapse.interval == 60
        assert timelapse.height is None
        assert timelapse.codec == TimelapseCodecEnum.h265
        assert timelapse.retain_days == 365

    def test_global_timelapse_propagates_to_camera(self):
        config_dict = dict(self.minimal)
        config_dict["timelapse"] = {"enabled": True, "interval": 120}
        config = FrigateConfig(**config_dict)
        timelapse = config.cameras["back"].timelapse
        assert timelapse.enabled is True
        assert timelapse.interval == 120

    def test_camera_override_takes_precedence(self):
        config_dict = dict(self.minimal)
        config_dict["timelapse"] = {"enabled": True, "interval": 120}
        config_dict["cameras"]["back"]["timelapse"] = {"interval": 30}
        config = FrigateConfig(**config_dict)
        timelapse = config.cameras["back"].timelapse
        # global enabled still applies, camera interval overrides
        assert timelapse.enabled is True
        assert timelapse.interval == 30


class TestTimelapseHighResInput(unittest.TestCase):
    def _config(self, inputs):
        cfg = {
            "mqtt": {"host": "mqtt"},
            "cameras": {
                "back": {
                    "ffmpeg": {"inputs": inputs},
                    "detect": {"height": 1080, "width": 1920, "fps": 5},
                }
            },
        }
        return FrigateConfig(**cfg).cameras["back"]

    def test_prefers_record_role(self):
        cam = self._config(
            [
                {"path": "rtsp://10.0.0.1/detect", "roles": ["detect"]},
                {"path": "rtsp://10.0.0.1/record", "roles": ["record"]},
            ]
        )
        url, input_args = get_highres_input(cam)
        assert url == "rtsp://10.0.0.1/record"
        assert input_args == ["-rtsp_transport", "tcp"]

    def test_falls_back_to_detect(self):
        cam = self._config([{"path": "http://10.0.0.1/detect", "roles": ["detect"]}])
        url, input_args = get_highres_input(cam)
        assert url == "http://10.0.0.1/detect"
        assert input_args == []


class TestTimelapseSpeedupFactor(unittest.TestCase):
    def test_factor_is_one_at_native_speed(self):
        # native speed = interval * segment fps (30); at 60s interval that is
        # 1800 real seconds per video second -> no retiming needed
        assert get_speedup_factor(1800, 60) == 1.0

    def test_factor_scales_with_requested_speed(self):
        # asking for 3600 (twice native) plays back twice as fast
        assert get_speedup_factor(3600, 60) == 2.0


class TestTimelapseSegmentBoundary(unittest.TestCase):
    def test_daily_segment_end_is_next_utc_midnight(self):
        ts = datetime.datetime(
            2026, 5, 23, 14, 30, 0, tzinfo=datetime.timezone.utc
        ).timestamp()
        expected = datetime.datetime(
            2026, 5, 24, 0, 0, 0, tzinfo=datetime.timezone.utc
        ).timestamp()
        assert get_segment_end(ts) == expected

from __future__ import annotations

import logging
import unittest

from ycbot.utils import RetryError
from ycbot.utils.logger import format_error, log_error, log_event
from ycbot.yc.client import YcApiError


class LoggingTests(unittest.TestCase):
    def test_format_error_extracts_yc_payload_message_from_retry_error(self) -> None:
        api_error = YcApiError(
            status=403,
            message="request failed",
            payload='{"code":7,"message":"Permission denied","details":[{"message":"extra"}]}',
        )
        try:
            raise RetryError(str(api_error)) from api_error
        except RetryError as exc:
            text = format_error(exc)

        self.assertEqual("403 request failed: Permission denied", text)
        self.assertNotIn("details", text)

    def test_log_error_uses_short_error_text(self) -> None:
        logger = logging.getLogger("test.short.log")
        api_error = YcApiError(
            status=400,
            message="request failed",
            payload='{"code":9,"message":"Cloud is currently being deleted","details":[{"requestId":"abc"}]}',
        )

        with self.assertLogs(logger, level="INFO") as logs:
            log_error(logger, "cloud.delete.error", api_error, cloud_id="cloud-1")

        line = logs.output[0]
        self.assertIn("error=400 request failed: Cloud is currently being deleted", line)
        self.assertNotIn("requestId", line)

    def test_log_event_truncates_long_string_fields(self) -> None:
        logger = logging.getLogger("test.short.fields")

        with self.assertLogs(logger, level="INFO") as logs:
            log_event(logger, "event", error="x" * 500)

        self.assertLess(len(logs.output[0]), 380)

import unittest
from unittest.mock import MagicMock, patch

from smart_monitor.notify import NotifyError, send_ntfy


class NtfyTests(unittest.TestCase):
    def test_empty_topic(self):
        with self.assertRaises(NotifyError):
            send_ntfy("https://ntfy.sh", "", "t", "m")

    def test_posts_headers(self):
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = b'{"id":"x"}'
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        with patch("smart_monitor.notify.urllib.request.urlopen", return_value=resp) as opener:
            send_ntfy(
                "https://ntfy.sh",
                "topic-name",
                "SMART FAIL on box",
                "pending sectors",
                priority=5,
                tags="rotating_light,hard_disk",
                token="secret",
            )
        req = opener.call_args[0][0]
        self.assertEqual(req.full_url, "https://ntfy.sh/topic-name")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.headers["Title"], "SMART FAIL on box")
        self.assertEqual(req.headers["Priority"], "5")
        self.assertTrue(req.headers["Authorization"].endswith("secret"))

import unittest

from app import PAIRING_TOKEN, app


class PlaywrightRouteSafetyTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.headers = {"X-PathPilot-Token": PAIRING_TOKEN}

    def test_capture_rejects_non_https_tab_before_browser_launch(self):
        response = self.client.post(
            "/api/teach/capture",
            json={
                "active_tab_url": "http://www.lifeatflytbase.com/",
                "workflow_title": "Unsafe test",
                "visible_link_text": "VIEW OPENINGS",
            },
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "teach_requires_https_active_tab")


if __name__ == "__main__":
    unittest.main()

import unittest

from playwright_executor import (
    PlaywrightSafetyError,
    assert_same_origin_https,
    build_readonly_navigation_workflow,
)


class PlaywrightExecutorSafetyTests(unittest.TestCase):
    def setUp(self):
        self.policy = {
            "site_id": "www-lifeatflytbase-com",
            "display_name": "FlytBase public site",
            "allowed_domains": ["www.lifeatflytbase.com"],
            "default_action_mode": "read_only",
            "allowed_actions": ["navigate", "view", "scroll"],
            "prohibited_actions": ["delete", "publish", "send_email"],
        }

    def test_allows_https_url_on_exact_policy_domain(self):
        self.assertEqual(
            assert_same_origin_https("https://www.lifeatflytbase.com/about", self.policy),
            "https://www.lifeatflytbase.com/about",
        )

    def test_rejects_http_and_cross_origin_navigation(self):
        for url in ("http://www.lifeatflytbase.com/about", "https://console.flytbase.com/"):
            with self.subTest(url=url):
                with self.assertRaises(PlaywrightSafetyError):
                    assert_same_origin_https(url, self.policy)

    def test_builds_only_verified_readonly_navigation_steps(self):
        workflow = build_readonly_navigation_workflow(
            policy=self.policy,
            workflow_id="flytbase-public-about",
            title="Explore the FlytBase About page",
            start_url="https://www.lifeatflytbase.com/",
            verified_steps=[
                {
                    "name": "Open About",
                    "from_url": "https://www.lifeatflytbase.com/",
                    "to_url": "https://www.lifeatflytbase.com/about",
                    "visible_link_text": "ABOUT",
                    "verification_text": "About page heading is visible",
                }
            ],
        )
        self.assertEqual(workflow["status"], "draft")
        self.assertEqual(workflow["steps"][0]["action_safety_level"], "read_only")
        self.assertEqual(workflow["steps"][0]["action"]["type"], "navigate")
        self.assertNotIn("selector", workflow["steps"][0]["action"])


if __name__ == "__main__":
    unittest.main()

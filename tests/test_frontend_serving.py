"""FastAPI serves the production React build and reserves API paths."""

import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from main import app


class TestFrontendServing(unittest.TestCase):
    def test_production_bundle_contains_builtin_account_forms(self):
        assets = Path(__file__).resolve().parents[1] / "frontend" / "dist" / "assets"
        bundle = "".join(path.read_text(encoding="utf-8") for path in assets.glob("*.js"))

        self.assertIn("/api/auth/", bundle)
        self.assertIn("Create account", bundle)
        self.assertNotRegex(bundle, r"pk_(?:test|live)_[A-Za-z0-9_-]{20,}")

    def test_root_and_spa_route_serve_react_build(self):
        client = TestClient(app)
        root = client.get("/")
        route = client.get("/planner")

        self.assertEqual(root.status_code, 200)
        self.assertIn("/assets/", root.text)
        self.assertNotIn("CLERK_PUBLISHABLE_KEY", root.text)
        self.assertEqual(route.status_code, 200)
        self.assertEqual(route.text, root.text)

    def test_bundled_assets_are_served_and_unknown_assets_are_not_spa_pages(self):
        client = TestClient(app)
        image = client.get("/images/hakone.jpg")
        missing_asset = client.get("/assets/missing.js")
        api_path = client.get("/api/not-a-route")

        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.headers["content-type"], "image/jpeg")
        self.assertEqual(missing_asset.status_code, 404)
        self.assertEqual(api_path.status_code, 404)


if __name__ == "__main__":
    unittest.main()

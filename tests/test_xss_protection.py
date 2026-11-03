# Test XSS protection integration
from pathlib import Path
import unittest

class TestXSSProtection(unittest.TestCase):
    def test_react_markdown_renderer_uses_dompurify_with_restrictions(self):
        source = Path(__file__).resolve().parents[1] / "frontend" / "src" / "Planner.tsx"
        content = source.read_text(encoding="utf-8")
        self.assertIn("DOMPurify.sanitize", content)
        self.assertIn("FORBID_TAGS", content)
        self.assertIn("ALLOWED_URI_REGEXP", content)

if __name__ == "__main__":
    unittest.main()

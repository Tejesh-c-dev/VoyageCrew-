# Test XSS protection integration
import re
import unittest

class TestXSSProtection(unittest.TestCase):
    def test_script_contains_dompurify_sanitize(self):
        with open(r"c:/Agents/tripmate/static/script.js", "r", encoding="utf-8") as f:
            content = f.read()
        # Check that DOMPurify.sanitize is used in renderMarkdown
        pattern = re.compile(r"DOMPurify\.sanitize\s*\(", re.MULTILINE)
        self.assertRegex(content, pattern, "DOMPurify.sanitize call not found in script.js")

    def test_index_includes_dompurify_cdn(self):
        with open(r"c:/Agents/tripmate/templates/index.html", "r", encoding="utf-8") as f:
            html = f.read()
        self.assertIn("cdnjs.cloudflare.com/ajax/libs/dompurify", html, "DOMPurify CDN script tag missing in index.html")

if __name__ == "__main__":
    unittest.main()

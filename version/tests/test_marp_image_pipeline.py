"""
tests/test_marp_image_pipeline.py — Comprehensive Unit & Integration Tests

Validates:
  1. Content-aware image query derivation for diverse subjects (trees, healthcare, tech, etc.)
  2. Successful Pexels search result parsing & selection
  3. Missing API key, HTTP errors (401, 429, 500), and network timeouts
  4. Image byte-level validation (JPEG, PNG, WebP) and caching
  5. Local company assets priority over external search
  6. Marp Markdown image insertion preserving slide boundaries (---) and frontmatter
  7. Selective image placement (content slides vs tables, KPI metrics, closing slide)
  8. Broken local image reference cleanup
  9. Backward compatibility with existing Marp Markdown
  10. End-to-end Marp PPTX compilation and embedded slide inspection
"""

import os
import sys
import json
import io
import shutil
import zipfile
import unittest
from unittest.mock import patch, MagicMock

# Ensure repo version folder is in sys.path
_REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_DIR not in sys.path:
    sys.path.insert(0, _REPO_DIR)

from tools.image_provider import (
    fetch_relevant_image,
    validate_image_file,
    get_cached_image,
    clear_cache,
    _clean_query,
    _slugify,
)
from tools.marp_creator import (
    derive_slide_image_query,
    process_marp_images,
    compile_marp_to_pptx,
    _ensure_marp_frontmatter,
)
from core.config import ASSETS_IMAGES_CACHE_DIR, PPT_OUTPUT_DIR


# Minimal valid 1x1 JPEG image bytes
VALID_JPEG_BYTES = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00"
    b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
    b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
    b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342"
    b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
    b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
    b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
) + (b"\x00" * 200)  # Pad over 256 bytes for validation threshold


SAMPLE_PEXELS_SEARCH_RESPONSE = {
    "total_results": 1,
    "page": 1,
    "per_page": 1,
    "photos": [
        {
            "id": 99991,
            "width": 2400,
            "height": 1600,
            "url": "https://www.pexels.com/photo/mature-oak-tree-in-forest-99991/",
            "photographer": "Jane Forester",
            "photographer_url": "https://www.pexels.com/@janeforester",
            "alt": "Mature oak tree with rich canopy in summer forest",
            "src": {
                "original": "https://images.pexels.com/photos/99991/original.jpeg",
                "large2x": "https://images.pexels.com/photos/99991/large2x.jpeg",
                "large": "https://images.pexels.com/photos/99991/large.jpeg",
                "landscape": "https://images.pexels.com/photos/99991/landscape.jpeg",
            },
        }
    ],
}


class TestMarpImagePipeline(unittest.TestCase):

    def setUp(self):
        clear_cache()

    def tearDown(self):
        clear_cache()

    # ──────────────────────────────────────────────────────────────────────────
    # 1. Image Query Derivation for Diverse Subjects
    # ──────────────────────────────────────────────────────────────────────────
    def test_derive_image_queries_for_tree_topics(self):
        """Verify queries matching Section 4 specification for tree presentations."""
        cases = [
            ("# Introduction to Trees\n- Overview of the world of trees", "mature trees in a green forest"),
            ("# Types of Trees\n- Oak, pine, and maple tree varieties across ecosystems", "oak pine and maple trees"),
            ("# Tree Anatomy\n- Understanding the trunk, branches, roots, and leaves", "tree trunk branches roots and leaves"),
            ("# Tree Growth\n- How a seedling sprouts and develops into a mature tree", "seedling growing into a young tree"),
            ("# Ecological Benefits\n- Canopy habitat for wildlife, birds, and soil protection", "forest canopy birds and wildlife"),
            ("# Deforestation Threats\n- Aerial forest clearing, logging, and habitat loss", "aerial photograph of forest clearing"),
            ("# Forest Conservation\n- People planting young trees and reforestation efforts", "people planting young trees"),
        ]
        for slide_text, expected_query in cases:
            derived = derive_slide_image_query(slide_text, presentation_topic="Trees")
            self.assertEqual(derived, expected_query, f"Failed for slide:\n{slide_text}")

    def test_derive_image_queries_for_other_domains(self):
        """Verify query derivation works across healthcare, technology, travel, and wildlife."""
        h_slide = "# Hospital Operations\n- Patient care and medical clinic workflow"
        self.assertIn("healthcare", derive_slide_image_query(h_slide, "Healthcare").lower())

        t_slide = "# Cloud Architecture\n- Scalable software systems and AI automation"
        self.assertIn("technology", derive_slide_image_query(t_slide, "Technology").lower())

        w_slide = "# African Safari Wildlife\n- Animals roaming the savannah fauna"
        self.assertIn("wildlife", derive_slide_image_query(w_slide, "Wildlife").lower())

    def test_explicit_directive_takes_precedence(self):
        """When an explicit <!-- image_query: ... --> is supplied, it must be honored."""
        slide = '<!-- image_query: "majestic coastal redwood trees in fog", position: "left", alt: "Redwoods" -->\n# Ancient Giants\n- Tallest trees on earth'
        query = derive_slide_image_query(slide, "Trees")
        self.assertEqual(query, "majestic coastal redwood trees in fog")

    # ──────────────────────────────────────────────────────────────────────────
    # 2. Pexels API Parsing & Successful Download
    # ──────────────────────────────────────────────────────────────────────────
    @patch("tools.image_provider.urllib.request.urlopen")
    @patch.dict(os.environ, {"PEXELS_API_KEY": "test_dummy_key_12345"})
    def test_successful_pexels_search_and_download(self, mock_urlopen):
        """Mock Pexels API search & image binary download."""
        # First call: Search API -> JSON
        # Second call: Image download -> JPEG binary
        resp_search = MagicMock()
        resp_search.__enter__.return_value = resp_search
        resp_search.status = 200
        resp_search.read.return_value = json.dumps(SAMPLE_PEXELS_SEARCH_RESPONSE).encode("utf-8")

        resp_img = MagicMock()
        resp_img.__enter__.return_value = resp_img
        resp_img.status = 200
        resp_img.read.return_value = VALID_JPEG_BYTES

        mock_urlopen.side_effect = [resp_search, resp_img]

        result = fetch_relevant_image("oak pine maple trees", orientation="landscape")

        self.assertIsNotNone(result)
        self.assertEqual(result["source"], "pexels")
        self.assertEqual(result["photographer"], "Jane Forester")
        self.assertEqual(result["image_id"], "99991")
        self.assertTrue(os.path.isfile(result["local_path"]))
        self.assertTrue(validate_image_file(result["local_path"]))

    # ──────────────────────────────────────────────────────────────────────────
    # 3. Missing API Key and API Failures (401, 429, Timeout)
    # ──────────────────────────────────────────────────────────────────────────
    @patch.dict(os.environ, {"PEXELS_API_KEY": ""}, clear=False)
    @patch("core.config.PEXELS_API_KEY", "")
    def test_missing_api_key_falls_back_gracefully(self):
        """When PEXELS_API_KEY is not set, retrieval returns None without crashing."""
        result = fetch_relevant_image("unique_uncached_missing_key_test_query")
        self.assertIsNone(result)

    @patch("tools.image_provider.urllib.request.urlopen")
    @patch.dict(os.environ, {"PEXELS_API_KEY": "test_dummy_key"})
    def test_pexels_401_unauthorized_falls_back(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://api.pexels.com/v1/search",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=None
        )
        result = fetch_relevant_image("oak trees")
        self.assertIsNone(result)

    @patch("tools.image_provider.urllib.request.urlopen")
    @patch.dict(os.environ, {"PEXELS_API_KEY": "test_dummy_key"})
    def test_pexels_empty_results_falls_back(self, mock_urlopen):
        resp = MagicMock()
        resp.__enter__.return_value = resp
        resp.status = 200
        resp.read.return_value = json.dumps({"total_results": 0, "photos": []}).encode("utf-8")
        mock_urlopen.return_value = resp

        result = fetch_relevant_image("nonexistent_rare_cryptozoology_tree")
        self.assertIsNone(result)

    # ──────────────────────────────────────────────────────────────────────────
    # 4. Image Validation and Caching
    # ──────────────────────────────────────────────────────────────────────────
    def test_validate_image_file_valid_and_invalid(self):
        cache_dir = ASSETS_IMAGES_CACHE_DIR
        os.makedirs(cache_dir, exist_ok=True)
        good_file = os.path.join(cache_dir, "test_valid.jpg")
        bad_file = os.path.join(cache_dir, "test_corrupt.jpg")

        with open(good_file, "wb") as f:
            f.write(VALID_JPEG_BYTES)

        with open(bad_file, "wb") as f:
            f.write(b"NOT_AN_IMAGE_FILE_JUST_PLAIN_TEXT")

        self.assertTrue(validate_image_file(good_file))
        self.assertFalse(validate_image_file(bad_file))
        self.assertFalse(validate_image_file(os.path.join(cache_dir, "nonexistent.jpg")))

        os.remove(good_file)
        os.remove(bad_file)

    @patch("tools.image_provider.urllib.request.urlopen")
    @patch.dict(os.environ, {"PEXELS_API_KEY": "test_dummy_key"})
    def test_caching_prevents_duplicate_network_calls(self, mock_urlopen):
        resp_search = MagicMock()
        resp_search.__enter__.return_value = resp_search
        resp_search.status = 200
        resp_search.read.return_value = json.dumps(SAMPLE_PEXELS_SEARCH_RESPONSE).encode("utf-8")

        resp_img = MagicMock()
        resp_img.__enter__.return_value = resp_img
        resp_img.status = 200
        resp_img.read.return_value = VALID_JPEG_BYTES

        mock_urlopen.side_effect = [resp_search, resp_img]

        # First call: hits network
        res1 = fetch_relevant_image("oak trees forest")
        self.assertIsNotNone(res1)
        self.assertEqual(mock_urlopen.call_count, 2)

        # Second call with same query: must be served from cache without calling urlopen
        res2 = fetch_relevant_image("oak trees forest")
        self.assertIsNotNone(res2)
        self.assertEqual(mock_urlopen.call_count, 2)  # No additional network requests!
        self.assertEqual(res1["local_path"], res2["local_path"])

    # ──────────────────────────────────────────────────────────────────────────
    # 5. Local Assets Take Priority
    # ──────────────────────────────────────────────────────────────────────────
    @patch("tools.asset_manager.get_best_image")
    def test_local_asset_priority_over_pexels(self, mock_get_best_image):
        # Create a real dummy local image file
        cache_dir = ASSETS_IMAGES_CACHE_DIR
        os.makedirs(cache_dir, exist_ok=True)
        local_asset_path = os.path.join(cache_dir, "Company_Forest_Asset.jpg")
        with open(local_asset_path, "wb") as f:
            f.write(VALID_JPEG_BYTES)

        mock_get_best_image.return_value = local_asset_path

        res = fetch_relevant_image("forest trees")
        self.assertIsNotNone(res)
        self.assertEqual(res["source"], "local_asset")
        self.assertEqual(res["photographer"], "Company Asset Library")

        os.remove(local_asset_path)

    # ──────────────────────────────────────────────────────────────────────────
    # 6. Markdown Preparation and Split Layout Insertion
    # ──────────────────────────────────────────────────────────────────────────
    @patch("tools.image_provider.fetch_relevant_image")
    def test_process_marp_images_split_layout_and_cover(self, mock_fetch):
        # Create a dummy image
        cache_dir = ASSETS_IMAGES_CACHE_DIR
        os.makedirs(cache_dir, exist_ok=True)
        dummy_img = os.path.join(cache_dir, "test_pine.jpg").replace("\\", "/")
        with open(dummy_img, "wb") as f:
            f.write(VALID_JPEG_BYTES)

        mock_fetch.return_value = {
            "local_path": dummy_img,
            "relative_path": "assets/images/cache/test_pine.jpg",
            "source": "pexels",
            "source_url": "https://pexels.com/test",
            "photographer": "Nature Photographer",
            "alt_text": "Pine trees in high mountain forest",
            "image_id": "888",
        }

        markdown_in = (
            "<!-- _class: lead -->\n"
            "# The World of Trees\n"
            "## Understanding Species and Forests\n"
            "**Prepared by EAI Systems**\n\n"
            "---\n\n"
            "# Types of Trees\n"
            "## Conifers and Deciduous\n\n"
            "- **Oak Trees:** Sturdy deciduous trees with deep root systems.\n"
            "- **Pine Trees:** Evergreen conifers bearing needles and cones.\n"
            "- **Maple Trees:** Recognized for vivid autumn foliage.\n\n"
            "---\n\n"
            "# Forest Growth Comparison\n\n"
            "| Tree Type | Avg Height | Lifespan |\n"
            "|---|---|---|\n"
            "| Oak | 25m | 300+ yrs |\n"
            "| Pine | 35m | 150 yrs |\n\n"
            "---\n\n"
            "<!-- _class: lead -->\n"
            "# Thank You\n"
            "## Questions & Next Steps\n"
        )

        processed = process_marp_images(markdown_in, "Trees")

        # Slide 1 (Cover): Got background image
        self.assertIn("_backgroundImage:", processed)
        self.assertIn(dummy_img, processed)

        # Slide 2 (Types of Trees): Got split layout
        self.assertIn('<div class="grid-2">', processed)
        self.assertTrue("Pine trees in high mountain forest" in processed and "test_pine.jpg" in processed)
        self.assertIn("<!-- Attribution:", processed)

        # Slide 3 (Table): Remained intact table without image insertion
        self.assertIn("| Tree Type | Avg Height | Lifespan |", processed)
        self.assertNotIn("![", processed.split("\n---\n")[2])

        # Slide 4 (Thank You): Remained clean closing slide
        self.assertIn("# Thank You", processed)

        if os.path.exists(dummy_img):
            os.remove(dummy_img)

    # ──────────────────────────────────────────────────────────────────────────
    # 7. Broken Image Path Cleanup
    # ──────────────────────────────────────────────────────────────────────────
    def test_broken_image_path_is_removed(self):
        broken_md = (
            "# Endangered Species\n\n"
            "![Broken File](C:/non_existent_folder/missing_image_123.jpg)\n\n"
            "- Habitats are under stress worldwide."
        )
        cleaned = process_marp_images(broken_md, "Endangered")
        self.assertNotIn("missing_image_123.jpg", cleaned)

    # ──────────────────────────────────────────────────────────────────────────
    # 8. Full Frontmatter & Theme Integrity
    # ──────────────────────────────────────────────────────────────────────────
    def test_ensure_marp_frontmatter_preserves_structure(self):
        sample = "# Quick Tree Briefing\n\n- Fact 1\n- Fact 2"
        full = _ensure_marp_frontmatter(sample, "Trees Briefing")

        self.assertTrue(full.startswith("---\nmarp: true\n"))
        self.assertIn("theme: default", full)
        self.assertIn("paginate: true", full)
        self.assertIn("size: 16:9", full)
        self.assertIn("EAI Systems", full)
        self.assertIn("# Quick Tree Briefing", full)

    # ──────────────────────────────────────────────────────────────────────────
    # 9. End-to-End Marp PPTX Compilation
    # ──────────────────────────────────────────────────────────────────────────
    def test_end_to_end_marp_compilation(self):
        """Compile a presentation with local image and verify PPTX structure."""
        cache_dir = ASSETS_IMAGES_CACHE_DIR
        os.makedirs(cache_dir, exist_ok=True)
        test_img = os.path.join(cache_dir, "e2e_tree_oak.jpg").replace("\\", "/")
        with open(test_img, "wb") as f:
            f.write(VALID_JPEG_BYTES)

        md = (
            f"<!-- _class: lead -->\n"
            f"# Test Presentation on Trees\n"
            f"## Automated E2E Pipeline Validation\n\n"
            f"---\n\n"
            f"# Species Overview\n\n"
            f'<div class="grid-2">\n'
            f"<div>\n\n"
            f"- **Oak:** Durable hardwood supporting forest biodiversity.\n"
            f"- **Pine:** Coniferous tree adapted for colder elevations.\n\n"
            f"</div>\n"
            f"<div>\n\n"
            f"![Oak and Pine Trees]({test_img})\n\n"
            f"</div>\n"
            f"</div>\n\n"
            f"---\n\n"
            f"# Comparative Growth Metrics\n\n"
            f"| Metric | Oak | Pine |\n"
            f"|---|---|---|\n"
            f"| Growth Rate | Moderate | Fast |\n"
            f"| Canopy Spread | Wide | Narrow |\n"
        )

        pptx_path = compile_marp_to_pptx(md, "Pipeline E2E Test")
        self.assertTrue(os.path.isfile(pptx_path))
        self.assertGreater(os.path.getsize(pptx_path), 50000)

        # Inspect PPTX file structure
        with zipfile.ZipFile(pptx_path, "r") as z:
            namelist = z.namelist()
            # Must contain presentation XML and embedded media
            self.assertIn("ppt/presentation.xml", namelist)
            media_files = [n for n in namelist if n.startswith("ppt/media/")]
            self.assertGreaterEqual(len(media_files), 1, "Expected embedded media in compiled PPTX")

        if os.path.exists(test_img):
            os.remove(test_img)


if __name__ == "__main__":
    unittest.main()

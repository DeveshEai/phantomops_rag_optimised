"""
version/engines/presentation/ — High-Impact Marp Presentation Engine & 10-Bundle Rota
Combines Marp CLI compilation, dynamic Theme + Layout paired rotation, and Pexels real photo injection.
"""

from tools.marp_creator import (
    compile_marp_to_pptx,
    process_marp_images,
    derive_slide_image_query,
    MARP_THEME_STYLE,
)
from tools.ppt_creator import create_presentation
from tools.ppt_rota import (
    get_next_ppt_bundle,
    peek_current_bundle,
    get_bundle_by_id,
    reset_rota,
    PPT_TEMPLATES_REGISTRY,
)
from tools.ppt_reviewer import review_presentation
from tools.image_provider import fetch_relevant_image

__all__ = [
    "compile_marp_to_pptx",
    "create_presentation",
    "process_marp_images",
    "derive_slide_image_query",
    "MARP_THEME_STYLE",
    "get_next_ppt_bundle",
    "peek_current_bundle",
    "get_bundle_by_id",
    "reset_rota",
    "PPT_TEMPLATES_REGISTRY",
    "review_presentation",
    "fetch_relevant_image",
]

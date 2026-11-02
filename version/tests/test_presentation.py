"""
test_presentation.py — Test Real Image Presentation Generation on ANY Topic

Usage:
    python test_presentation.py "Your Topic Here"

Examples:
    python test_presentation.py "Robotics in Modern Healthcare"
    python test_presentation.py "Space Exploration and Mars Rovers"
    python test_presentation.py "African Wildlife and Safari Predators"
    python test_presentation.py "Trees: Types, Structure, and Growth"
"""

import os
import sys

# Ensure repo version folder is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.marp_creator import compile_marp_to_pptx, _ensure_marp_frontmatter

def build_test_deck(topic: str) -> str:
    """Generate a sample structured presentation on the given topic."""
    return f"""<!-- _class: lead -->
<!-- _header: "" -->
<!-- _footer: "" -->
<!-- _paginate: false -->

# {topic}
## Comprehensive Industry Overview & Strategic Analysis
**Prepared by EAI Systems**

---

# Core Fundamentals and Overview
## Foundational Principles and Real-World Scope

- **Core Significance:** Highlights key developments and essential drivers defining {topic.lower()}.
- **Operational Scope:** Modern approaches deployed across institutions and field environments.
- **Strategic Impact:** Removing bottlenecks and accelerating real-world outcomes.
- **Future Trajectory:** Rapidly evolving methodologies providing long-term value.

---

# Key Methods and Architecture
## Structural Components and Practical Delivery

- **Architectural Design:** Built for resilience, scalability, and seamless integration.
- **Advanced Methodologies:** Standardized techniques ensuring precision and high performance.
- **Resource Optimization:** Maximizing return on investment while minimizing overhead.
- **Practical Application:** Field-tested execution models delivering measurable results.

---

# Global Impact and Opportunities
## Scaling Adoption Across Diverse Sectors

- **Widespread Adoption:** Rapid integration across global organizations and researchers.
- **Economic Value:** Substantial cost reductions and accelerated execution timelines.
- **Environmental & Social Harmony:** Sustainable practices aligning with modern standards.
- **Collaborative Ecosystems:** Cross-industry partnerships fueling continuous innovation.

---

# Comparative Landscape
## Evaluating Key Models and Approaches

| Model / Approach | Scalability | Relative Complexity | Primary Advantage |
|---|---|---|---|
| Approach Alpha | High | Moderate | Rapid initial deployment |
| Approach Beta | Very High | Advanced | Maximum long-term resilience |
| Hybrid Model | High | Streamlined | Optimal cost-to-performance ratio |

---

<!-- _class: lead -->
<!-- _header: "" -->
<!-- _footer: "" -->
<!-- _paginate: false -->

# Thank You
## Questions & Next Steps
**EAI Systems Pvt. Ltd. | www.eaisystems.com**
Contact: info@eaisystems.com
"""

if __name__ == "__main__":
    topic = sys.argv[1] if len(sys.argv) > 1 else "Robotics and Artificial Intelligence"
    bundle_arg = None
    if len(sys.argv) > 2 and sys.argv[2].isdigit():
        bundle_arg = int(sys.argv[2])
    print(f"\n[Test] Generating presentation for topic: '{topic}'...")

    markdown_input = build_test_deck(topic)
    pptx_path = compile_marp_to_pptx(markdown_input, title=topic, bundle_id=bundle_arg)

    print("\n" + "=" * 60)
    print("SUCCESS! PowerPoint file generated:")
    print(f"Path: {pptx_path}")
    print(f"Size: {os.path.getsize(pptx_path):,} bytes")
    print("=" * 60 + "\n")

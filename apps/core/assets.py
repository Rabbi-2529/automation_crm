"""Front-end libraries, listed once in frontend/assets.json and used by the {% asset %} tag and the Docker build.

Development loads them from CDNs. Production (COMPILED_ASSETS) serves copies built into the image under
static/vendor/ (frontend/build.mjs), and a compiled Tailwind stylesheet instead of the in-browser compiler.
"""

import json
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
_MANIFEST = json.loads((FRONTEND / "assets.json").read_text(encoding="utf-8"))

LIBRARIES = _MANIFEST["libraries"]  # name -> {"cdn", "package", "file", "vendor"}
TAILWIND_BROWSER = _MANIFEST["tailwind_browser"]
TAILWIND_SOURCE = FRONTEND / "tailwind.css"  # build input, not a static file
TAILWIND_OUTPUT = "css/app.css"  # static path written by the Docker build

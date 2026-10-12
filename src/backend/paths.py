import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIST = Path(os.environ.get("AGEATOPIA_OUTPUT_DIR") or ROOT / "dist").expanduser().resolve()


def output_path(value):
    path = Path(value).expanduser().resolve()
    if not path.is_relative_to(DIST.resolve()):
        raise ValueError("Generated output must be inside the repository's dist directory")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path

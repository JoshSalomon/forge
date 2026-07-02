"""Container tests conftest - adds containers/ to sys.path."""

import sys
from pathlib import Path

_containers_dir = str(Path(__file__).parents[3] / "containers")
if _containers_dir not in sys.path:
    sys.path.insert(0, _containers_dir)

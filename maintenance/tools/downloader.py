"""Compatibility alias for the canonical runtime downloader.

Maintenance consumers historically imported this module directly.  Keep that
import stable while ensuring there is only one module object and one transport
implementation in the process.
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from x86qw_runtime.io import downloader as _runtime_downloader


sys.modules[__name__] = _runtime_downloader

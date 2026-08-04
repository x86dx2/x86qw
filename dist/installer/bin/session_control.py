"""Compatibility alias for the canonical runtime session controller."""

from __future__ import annotations

import sys

from x86qw_runtime import session_control as _canonical


sys.modules[__name__] = _canonical

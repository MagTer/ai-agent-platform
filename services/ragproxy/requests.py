from __future__ import annotations

import sys

import requests as _requests

# Ensure `ragproxy.requests` resolves to the real `requests` module so tests can patch it.
sys.modules[__name__] = _requests

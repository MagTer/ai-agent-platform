from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "asyncio: mark test as requiring an asyncio event loop"
    )


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:
    if not inspect.iscoroutinefunction(pyfuncitem.obj):
        return None

    sig = inspect.signature(pyfuncitem.obj)
    bound_args = {
        name: pyfuncitem.funcargs[name]
        for name in sig.parameters
        if name in pyfuncitem.funcargs
    }

    coro = pyfuncitem.obj(**bound_args)
    try:
        asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(coro)
        finally:
            loop.close()
    return True

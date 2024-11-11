"""Replicates an equivalent to the deprecated @app.on_event('startup') and @app.on_event('shutdown')
functionality.

```py
@router.on_event('startup')
async def setup():
    ...  # setup code

@router.on_event('shutdown')
async def teardown():
    ...  # teardown code
```

becomes

```py
from lifespan import lifespan_handler

@lifespan_handler
async def handle_lifespan():
    ...  # setup code
    yield
    ...  # teardown code
```
"""

from fastapi import FastAPI
from typing import Callable, AsyncGenerator, List
from contextlib import asynccontextmanager

from error_middleware import handle_error

_registered_handlers: List[Callable[[], AsyncGenerator]] = []
_already_started = False


def lifespan_handler(func: Callable[[], AsyncGenerator]) -> None:
    """Decorator to register a lifespan handler."""
    assert _already_started is False
    global _registered_handlers
    _registered_handlers.append(func)


@asynccontextmanager
async def top_level_lifespan_handler(app: FastAPI):
    """A context manager that runs all registered lifespan handlers."""
    global _registered_handlers
    global _already_started

    assert _already_started is False

    _already_started = True
    iters = [func().__aiter__() for func in _registered_handlers]
    for iter in iters:
        await iter.__anext__()

    yield

    errors: List[Exception] = []

    for iter in iters:
        try:
            await iter.__anext__()
        except StopAsyncIteration:
            pass
        except Exception as e:
            await handle_error(e)
        else:
            errors.append(RuntimeError("Lifespan handler did not stop after yield"))

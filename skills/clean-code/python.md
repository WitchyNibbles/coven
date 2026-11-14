# Python conventions (3.12+)

The repo's `.coven` standard wins. Never add a suppression, `Any`, `cast` or a weaker check.
The tools enforce annotation, PEP 604/695 syntax, exhaustive `match`, bare `except` and mutable
defaults; don't fight them. A rule sits under a cited heading only if the linked page states it;
the rest are under "Conventions".

## Idioms (https://peps.python.org/pep-0008/)
- Name: `snake_case` functions and variables, `PascalCase` classes, `UPPER_CASE` constants.
- Where a check-then-use can race (`if key in mapping: return mapping[key]`), use EAFP: try, then catch the specific exception (https://docs.python.org/3/glossary.html).
- Use `with` for cleanup: `__exit__` always runs once `__enter__` returns (https://docs.python.org/3/reference/compound_stmts.html).
- Pass variable data to `logging` calls as arguments to the format string; formatting is deferred (https://docs.python.org/3/howto/logging.html).
- `@dataclass(frozen=True)` makes instances read-only and `slots=True` generates `__slots__` (https://docs.python.org/3/library/dataclasses.html).

## Typing (https://typing.python.org/en/latest/reference/best_practices.html)
- Take abstract parameter types (`Sequence`, `Mapping`, `Iterable`); return concrete ones (`list`, `dict`).
- Mark names that must not be rebound with `Final` (https://docs.python.org/3/library/typing.html).

## Errors (https://docs.python.org/3/tutorial/errors.html)
- Catch the narrowest type around the fewest lines; let unexpected exceptions propagate.
- Re-raise with `raise NewError(...) from err` to keep the cause; use bare `raise` to propagate.
- Derive custom exceptions from `Exception`, named `...Error`.
- Put cleanup in a `finally` clause (it runs whether or not the `try` raises) or a `with` block.

## pydantic v2 (https://pydantic.dev/docs/validation/latest/concepts/models/)
- Use `model_validate` for objects and `model_validate_json` for raw JSON.
- Set `extra="forbid"` where undeclared fields must be rejected.
- `frozen=True` makes instance mutation a type-checking error: set it on value models (https://pydantic.dev/docs/validation/latest/concepts/config/).
- Write field rules as `field_validator` or `Annotated` validators (https://pydantic.dev/docs/validation/latest/concepts/validators/).

## FastAPI (https://fastapi.tiangolo.com/tutorial/bigger-applications/)
- Split a larger app into `APIRouter` modules wired with `app.include_router()`.
- Annotate the return type with the response model; add `response_model=` only when the return type and response model differ (https://fastapi.tiangolo.com/tutorial/response-model/).
- Reuse one `Annotated[T, Depends(fn)]` alias for a shared dependency (https://fastapi.tiangolo.com/tutorial/dependencies/).
- Use plain `def` (run in the threadpool) for a blocking library; use `async def` when the library supports `await` (https://fastapi.tiangolo.com/async/).

## SQLAlchemy 2 (https://docs.sqlalchemy.org/en/20/orm/session_basics.html)
- Open one `Session` per web request or job; commit or roll back at its boundary; never share it across threads or tasks.
- Declare columns with `Mapped[T]` and `mapped_column()` (https://docs.sqlalchemy.org/en/20/orm/declarative_tables.html).
- Select with `select()`; `Query` is legacy (https://docs.sqlalchemy.org/en/20/orm/queryguide/query.html).
- Lazy loading in a loop issues N+1 SELECTs: eager-load with `selectinload` (collections) or `joinedload` (https://docs.sqlalchemy.org/en/20/orm/queryguide/relationships.html).

## MCP servers (https://modelcontextprotocol.io/docs/develop/build-server)
- Never write to stdout in a stdio server (no `print`); log to stderr.
- Define tools with type hints and docstrings; the SDK generates the tool definition from them.
- Raise the SDK's `ToolError` (mcp 2.x: `from mcp.server.mcpserver.exceptions import ToolError`) for failures the model should see; a returned error string reads as success (https://py.sdk.modelcontextprotocol.io/servers/handling-errors/).

## Packaging (https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/)
- The `src/` layout prevents accidental imports of the in-development copy; the cost is an install step before running.

## pytest (https://docs.pytest.org/en/stable/how-to/fixtures.html)
- Use yield fixtures for setup and teardown.
- Use `@pytest.mark.parametrize` with `ids=` for input tables (https://docs.pytest.org/en/stable/example/parametrize.html).
- `tmp_path` is a temporary directory unique to each test (https://docs.pytest.org/en/stable/how-to/tmp_path.html); `monkeypatch` changes are undone after the test (https://docs.pytest.org/en/stable/how-to/monkeypatch.html).

## Conventions (no single source)
- Use a comprehension for one simple map or filter; use a loop when it stops reading as one expression.
- Return early instead of nesting `else`. Use `pathlib.Path`, not `os.path` string joins.
- `StrEnum` for closed sets shared across modules or persisted; `Literal` for a closed option local to one signature.
- Take `Protocol` for duck-typed parameters; use an ABC only when sharing implementation.
- Allow `except Exception` only at a top-level boundary that logs with `logger.exception` and stops or responds.
- Prefer `finally` or `with` over `except` plus re-raise for cleanup.
- Define one exception base per package. Raise for failure, return for absence.
- Validate once at the boundary, then pass the model, not a `dict`; use `TypedDict` or a model for structured data.
- `extra="forbid"` on requests your service receives; leave the default on models for upstream responses you consume.
- Never let `Any` leak into a public signature; parse untyped input into a typed model first.
- A handler that returns an ORM row for a differing `response_model`: annotate the real return type, not `Any`.
- Keep route handlers thin (parse, call a service function, map the result); keep queries in a repository or service.
- Define `__all__` only in modules that re-export; no logic in `__init__.py`.
- No I/O, network or env reads at import time; declarative objects (routers, app, loggers, `TypeAdapter`, MCP server instances) are fine.
- Read configuration once into a typed settings model and pass it in.
- Tests never touch the real home, cwd or environment; inject the HTTP client, clock and file access (FastAPI: `app.dependency_overrides`).
- Keep the action and the assertion in the test, not in a fixture. Never `sleep`; inject a clock or wait on an event.
- Use `pytest.raises(Specific, match=...)` for errors.

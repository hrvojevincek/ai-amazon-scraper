"""Top-level test config.

Why a `--run-integration` flag instead of always running everything?

Integration tests pull a Docker image (~150MB), spawn a Postgres container,
and run migrations — 10-15s of overhead before the first assertion. That's
fine on CI and on demand, but on every save during local TDD it kills your
loop. So they opt in.
"""

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="run tests under tests/integration/ (require docker)",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    # Auto-mark anything under tests/integration/ — saves writing the
    # decorator on every test.
    for item in items:
        if "tests/integration" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.integration)

    if config.getoption("--run-integration"):
        return

    skip = pytest.mark.skip(reason="integration test (use --run-integration)")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)

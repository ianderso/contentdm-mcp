"""Entry point: ``python -m contentdm_mcp`` / the ``contentdm-mcp`` script."""

from __future__ import annotations

from .server import run


def main() -> None:
    run()


if __name__ == "__main__":
    main()

"""Backward-compatible entrypoint for the deprecated SSE transport."""

from __future__ import annotations

import logging

from .remote import main as remote_main

logger = logging.getLogger(__name__)


def main() -> None:
    """Run the remote server and warn that SSE is deprecated."""

    logger.warning("notebooklm-mcp-sse now starts the Streamable HTTP server. SSE is deprecated.")
    remote_main()


if __name__ == "__main__":
    main()

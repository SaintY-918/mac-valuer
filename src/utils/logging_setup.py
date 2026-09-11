"""Logging config for entrypoints only.

Library modules just do `logger = logging.getLogger(__name__)` and never call
`basicConfig` themselves -- only whatever actually starts a process should
decide how logs are formatted. This was the same one-liner copy-pasted into
four separate entrypoints; centralising it means the format changes once.
"""

import logging


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")

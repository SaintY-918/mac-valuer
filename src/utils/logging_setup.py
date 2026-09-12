"""Logging config for entrypoints only.

Library modules just do `logger = logging.getLogger(__name__)` and never call
`basicConfig` themselves -- only whatever actually starts a process should
decide how logs are formatted. This was the same one-liner copy-pasted into
four separate entrypoints; centralising it means the format changes once.
"""

import logging
import sys


def configure_logging(level: int = logging.INFO) -> None:
    # Seller titles carry emoji, and a Windows console defaults to cp950,
    # which cannot encode them. The first such title printed in Step 2 raised
    # UnicodeEncodeError and killed a run that had already scraped everything.
    # The scheduled script sets PYTHONIOENCODING=utf-8; an ad-hoc run from a
    # terminal did not, so it is set here for both. errors="replace" rather
    # than utf-8 alone: a redirected stream can still be something narrower.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")

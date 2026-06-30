"""
Windows-safe RotatingFileHandler that handles multi-process file lock errors.

RotatingFileHandler on Windows can fail with PermissionError when multiple
processes try to rotate the same log file. This wrapper catches that error
and continues logging without rotation.
"""

from logging.handlers import RotatingFileHandler


class SafeRotatingFileHandler(RotatingFileHandler):
    """
    A RotatingFileHandler that gracefully handles Windows file lock errors.

    When rotation fails due to PermissionError (file in use by another process),
    this handler silently skips rotation and continues writing to the current log file.

    This is safe in multi-process environments where multiple agents share the same
    log file - only one process will successfully rotate, others will continue writing.
    """

    def doRollover(self):
        """
        Override doRollover to catch Windows file lock errors silently.
        """
        try:
            super().doRollover()
        except PermissionError:
            # File is locked by another process - skip rotation silently
            # This is expected in multi-agent environments
            pass
        except Exception as e:
            # Other unexpected errors - log to stderr but don't crash
            import sys

            print(f"[ERROR] Unexpected log rotation error: {e}", file=sys.stderr)

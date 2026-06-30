"""
@author: Zed
@file: tool.py
@time: 2025/7/29 13:36
@describe: Custom description
"""

import logging

from .log_setup import setup_logging

# --- Logging Setup ---
logger = logging.getLogger()  # Module-level logger
setup_logging(logger, "agent_run.log")

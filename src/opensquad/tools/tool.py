"""
@author: Zed
@file: tool.py
@time: 2025/7/29 13:36
@describe: Tool module logger setup
"""

import logging

from ..log_setup import setup_logging

# --- Logging Setup ---
logger = logging.getLogger("opensquad.tools")  # Named logger, avoid polluting root
setup_logging(logger, "agent_run.log")

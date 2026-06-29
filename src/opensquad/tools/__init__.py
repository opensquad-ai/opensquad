# -*- coding: utf-8 -*-
# Core framework tools (NOT pluginized, remain here)

import logging
logger = logging.getLogger(__name__)

# Use try-except for each import to prevent one broken module from blocking all tools
try:
    from . import system
except ImportError as e:
    logger.warning(f"Failed to import system tool: {e}")
    system = None

try:
    from . import filesystem
except ImportError as e:
    logger.warning(f"Failed to import filesystem tool: {e}")
    filesystem = None

try:
    from . import im
except ImportError as e:
    logger.warning(f"Failed to import im tool: {e}")
    im = None

try:
    from . import web
except ImportError as e:
    logger.warning(f"Failed to import web tool: {e}")
    web = None

# The following tools have been moved to plugins/:
#   websearch       -> plugins/websearch/
#   vision          -> plugins/vision/
#   media           -> plugins/media/
#   whisper_transcribe -> plugins/whisper/
#   mcp_query       -> plugins/mcp_query/
#   sequential_think -> plugins/sequential_think/

__all__ = [
    "system",
    "filesystem",
    "memory",
    "im",
    "web",
]

# -*- coding: utf-8 -*-

import os
import logging
from typing import Dict

logger = logging.getLogger("plugins.vision")


def _get_img_path_file() -> str:
    """Return the absolute path to img_path.txt (preferably placed in the agent's own directory)."""
    try:
        from opensquad.input_hub import input_hub
        if input_hub.agent_dir:
            return os.path.join(input_hub.agent_dir, "img_path.txt")
    except Exception:
        pass
    return "img_path.txt"


#     """
#     Perform string-based search and replace within a single file. When replacing code,
#     include some surrounding context lines (beyond just the replaced code) to ensure more precise replacement.
#
#     :param file_path: Path to the file.
#     :param search_str: The string to search for.
#     :param replace_str: The string to replace with.
#     """
# """
#     Call the WebSearch service's /search endpoint to retrieve search results for multiple queries.
#     Usage tips:
#     - **Concept expansion and synonym substitution**: When keywords fail to return useful results, try synonyms.
#       E.g., expand "artificial intelligence" to "machine learning", "deep learning", "neural networks", "LLM", etc.
#     - **Multi-angle queries:** For complex questions, don't rely on a single keyword. Use multiple related,
#       different-angle queries (the `queries` list) to get more comprehensive information.
#     - **Cross-validation of results:** Overlapping results from different queries generally indicate
#       more reliable sources.
#     - **Summary-driven content retrieval:** Don't visit all returned links directly. First read the
#       `snippet` carefully (summaries include date/time info), then select only the most relevant and
#       authoritative links, and use the `fetch` tool to retrieve the full text.
#     - **High result volume:** When fetch results are insufficient, increase max_results, e.g., 30 => 100.
#     demo: How to research "the latest advances in artificial intelligence"**
#     1.  **Define multi-angle queries:** `search(queries=["Latest AI breakthroughs 2024", "Top AI conference papers 2024", "Gartner 2024 AI report"], max_results=30)`.
#     2.  **Analyze summaries:** Review the `title` and `snippet` of the returned results, looking for
#         specific technologies (e.g., "multimodal large models", "AI Agent") or authoritative sources
#         (e.g., MIT, Google AI).
#     3.  **Precise content retrieval:** Pass the 2-3 most relevant `url` values to the `fetch` tool for in-depth reading.
#     4. **If querying only Chinese-language internet content, set contains_chinese=True.**
#     """
def read_image(image_path_list: list) -> Dict[str, str]:
    """
    Read local images at the specified absolute paths. The argument is a list;
    even a single image path should be placed in a list, e.g.: ['C:\\pic_01.jpg'].

    When your main model supports native vision (is_image=true), this tool passes
    the image path to the model so you can see and describe the image content.
    Use this when the user asks you to look at a specific file on local disk,
    or when you need to inspect an image downloaded by a previous action.

    Note: If the user sent an image directly in the chat interface, your native
    vision capability can already see it — do NOT call this tool in that case.

    After calling this tool, the system will inject the image paths into the
    conversation context in the same turn, and you will receive the image content
    on the next LLM response. Wait for the image analysis result.

    :param image_path_list: List of absolute paths to image files.
    :return: A dict indicating operation completion.
    """
    logger.info(f"[vision] read_image: {image_path_list}")

    try:
        # LLM may pass image_path_list as a JSON-encoded string like '["path1", "path2"]'
        # instead of a proper Python list. Detect and parse.
        if isinstance(image_path_list, str):
            import json as _json
            try:
                image_path_list = _json.loads(image_path_list)
            except Exception:
                image_path_list = [image_path_list]

        if not isinstance(image_path_list, list):
            image_path_list = [image_path_list]

        # Verify files exist
        import os as _os
        valid_paths = []
        for p in image_path_list:
            if _os.path.isfile(p):
                valid_paths.append(p)
            else:
                logger.warning(f"[vision] File not found: {p}")

        if not valid_paths:
            return {"status": "error", "message": "None of the specified image files exist."}

        # Write to img_path.txt for the Runner's next-turn reading (legacy path)
        img_path_file = _get_img_path_file()
        with open(img_path_file, "w", encoding="utf-8") as f:
            f.write(str(valid_paths))
        logger.info(f"[vision] img_path.txt -> {img_path_file}")

        # Also push to event_pipeline so the runner injects them immediately
        # in the same turn via tool result processing
        try:
            from opensquad.event_pipeline import event_pipeline
            event_pipeline.push_nowait(
                source="vision_tool",
                content=f"[Image injection requested: {valid_paths}]",
                metadata={
                    "image_paths": valid_paths,
                    "action": "inject_images",
                }
            )
        except Exception:
            pass

        return {
            "status": "success",
            "message": f"Image path(s) registered: {valid_paths}. The images will be passed to your vision model for analysis.",
            "image_paths": valid_paths,
        }

    except Exception as e:
        error_msg = f"Error reading image: {e}"
        logger.error(error_msg, exc_info=True)
        return {"status": "error", "message": error_msg}

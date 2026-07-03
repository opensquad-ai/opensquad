import inspect
import json
import re
import threading
from typing import Any, Union

from .log_setup import get_tool_call_debug_logger
from .tool import logger


class ToolRegistry:
    """Tool discovery and dispatch center"""

    def __init__(self):
        self._lock = threading.Lock()
        self._tools = {}  # {namespace: {"module": tool_set, "level": "core"/"extended"}}
        self._mcp_adapter = None
        self._desc_cache: str | None = None  # cache for generate_tool_descriptions()
        self._openai_tools_cache: dict[str, list[dict]] = {}  # tool_filter -> cached tools
        # Register help tool by default
        self.register(self, "help", level="core")

    def register(self, tool_set: object, namespace: str, level: str = "extended"):
        """Register a tool set. Warns if namespace already exists."""
        with self._lock:
            if namespace in self._tools:
                logger.warning(
                    "Tool namespace %r is being re-registered (old=%r, new=%r). This may indicate a plugin conflict.",
                    namespace,
                    self._tools[namespace]["module"].__class__.__name__,
                    tool_set.__class__.__name__,
                )
            self._tools[namespace] = {"module": tool_set, "level": level}
        self._desc_cache = None  # invalidate cache
        self._openai_tools_cache.clear()
        logger.info(f"Tool set '{namespace}' registered as [{level}].")

    def unregister(self, namespace: str):
        """Remove a tool set by namespace. Returns True if removed."""
        with self._lock:
            if namespace in self._tools:
                del self._tools[namespace]
                self._desc_cache = None  # invalidate cache
                self._openai_tools_cache.clear()
                logger.info(f"Tool set '{namespace}' unregistered.")
                return True
            return False

    def register_mcp_adapter(self, adapter, level: str = "extended"):
        with self._lock:
            self._mcp_adapter = adapter
        self._desc_cache = None  # invalidate cache
        self._openai_tools_cache.clear()
        logger.info(f"MCP adapter registered as [{level}].")

    def log_inventory(self, boot_logger=None) -> list[str]:
        """Log the full set of registered tool namespaces.

        Called at boot completion so smoke tests / crash logs can verify the
        tool set is complete. A missing namespace here means a plugin failed
        to register silently — the class of bug where one plugin's exception
        cascaded and hid subsequent plugins' tools.

        Returns the namespace list (for assertions in smoke tests).
        """
        with self._lock:
            namespaces = sorted(self._tools.keys())
            mcp_attached = self._mcp_adapter is not None
        line = f"[Boot] ToolRegistry inventory: {namespaces} (mcp_adapter={'yes' if mcp_attached else 'no'})"
        if boot_logger is not None:
            boot_logger.info(line)
        else:
            logger.info(line)
        return namespaces

    def get_tool_help(self, namespace: str) -> str:
        """Get detailed documentation for a specific tool set"""
        with self._lock:
            if namespace in self._tools:
                return self._generate_single_tool_desc(namespace, self._tools[namespace]["module"], detail=True)

        if namespace.startswith("mcp__") and self._mcp_adapter:
            return self._generate_mcp_desc(detail=True, filter_server=namespace)

        return f"Error: Tool namespace '{namespace}' not found."

    def generate_tool_descriptions(self) -> str:
        """Generate the tool list used in the Prompt"""
        if self._desc_cache is not None:
            return self._desc_cache

        descriptions = []
        with self._lock:
            for namespace, info in self._tools.items():
                # Injection-level gate: keep "hidden" namespaces out of the
                # XML prompt text too, mirroring generate_openai_tools(). A
                # hidden namespace must not surface in either rendering, or
                # the two views of the tool set diverge.
                if info["level"] == "hidden":
                    continue
                desc = self._generate_single_tool_desc(namespace, info["module"], detail=(info["level"] == "core"))
                descriptions.append(desc)

        if self._mcp_adapter:
            descriptions.append(self._generate_mcp_desc(detail=False))

        result = "\n".join(descriptions)
        self._desc_cache = result
        return result

    def generate_openai_tools(self, tool_filter: str = "all") -> list[dict]:
        """
        Generate OpenAI Tools JSON Schema (for Native Function Calling)

        Args:
            tool_filter: tool filtering strategy
                - "all": all tools (default, backward compatible)
                - "baseline": high-frequency tools only (files, system, search, memory, MCP)
                - "high": high-frequency + medium-frequency tools only
                - List[str]: specify namespace list (e.g. ["filesystem", "git"])

        Returns:
            List of tool definitions in OpenAI format:
            [
                {
                    "type": "function",
                    "function": {
                        "name": "namespace.function_name",
                        "description": "Function description",
                        "parameters": {
                            "type": "object",
                            "properties": {...},
                            "required": [...]
                        }
                    }
                }
            ]
        """
        # Check cache (key uses repr() to handle both str and list tool_filter)
        cache_key = repr(tool_filter)
        if cache_key in self._openai_tools_cache:
            return list(self._openai_tools_cache[cache_key])

        # Define high-frequency and medium-frequency tool namespaces
        HIGH_FREQ_NAMESPACES = {"filesystem", "system", "websearch", "long_memory", "help"}
        MEDIUM_FREQ_NAMESPACES = {"git", "api_process", "vision", "mcp_query", "im", "media", "translate_tool"}

        # Determine which namespaces to include
        if tool_filter == "all":
            # All tools (default, backward compatible)
            included_namespaces = None  # None means include all
        elif tool_filter == "baseline":
            # Baseline: high-frequency tools + MCP only
            included_namespaces = HIGH_FREQ_NAMESPACES
        elif tool_filter == "high":
            # High-frequency + medium-frequency tools
            included_namespaces = HIGH_FREQ_NAMESPACES | MEDIUM_FREQ_NAMESPACES
        elif isinstance(tool_filter, list):
            # Custom namespace list
            included_namespaces = set(tool_filter)
        else:
            logger.warning(f"Unknown tool_filter: {tool_filter}, using 'all'")
            included_namespaces = None

        tools = []

        # Iterate over all registered tool sets
        with self._lock:
            for namespace, info in self._tools.items():
                # Apply filter
                if included_namespaces is not None and namespace not in included_namespaces:
                    continue

                # Injection-level gate: "hidden" namespaces are registered (so the
                # agent can still call them by name if needed) but kept OUT of the
                # LLM-facing tool schema to save per-turn input tokens. This is the
                # per-agent manual control lever: set tool_levels[ns]="hidden" in
                # config.json to drop that namespace's schema from every API call.
                # core/extended are both injected (they only differ in description
                # verbosity below).
                if info["level"] == "hidden":
                    continue

                module = info["module"]
                level = info["level"]
                seen_funcs = set()  # Dedup: skip aliases

                # Get both functions and methods (supports modules and class instances)
                members = list(inspect.getmembers(module, inspect.isfunction)) + list(
                    inspect.getmembers(module, inspect.ismethod)
                )

                for name, func in members:
                    # Skip private methods and already-seen function objects
                    if name.startswith("_"):
                        continue
                    if id(func) in seen_funcs:
                        continue
                    seen_funcs.add(id(func))

                    # Generate schema for a single tool
                    try:
                        # Decide description verbosity based on tool level
                        if level == "core":
                            # Core tools: detailed description (full docstring)
                            description = self._extract_function_description(func)
                        else:
                            # Extended tools: brief summary (first line only)
                            doc = inspect.getdoc(func)
                            if doc:
                                description = doc.strip().splitlines()[0]
                            else:
                                description = f"Tool function (use help.get_tool_help('{namespace}') for details)"

                        schema = {
                            "type": "function",
                            "function": {
                                "name": f"{namespace}__{name}",  # Use double underscore as namespace separator
                                "description": description,
                                "parameters": self._extract_parameters_schema(func),
                            },
                        }
                        tools.append(schema)
                    except Exception as e:
                        logger.warning(f"Failed to generate schema for {namespace}.{name}: {e}")

        # MCP tools (always included, since they are external services)
        if self._mcp_adapter:
            try:
                with self._lock:
                    mcp_tools = self._mcp_adapter.get_all_tools()
                # MCP tools are already in OpenAI format, add directly
                tools.extend(mcp_tools)
            except Exception as e:
                logger.warning(f"Failed to get MCP tools: {e}")

        # Cache the result for this tool_filter
        self._openai_tools_cache[cache_key] = list(tools)
        logger.info(f"Generated {len(tools)} tool definitions (filter: {tool_filter})")
        return tools

    def _extract_function_description(self, func) -> str:
        """Extract function description (from docstring)"""
        doc = inspect.getdoc(func)
        if not doc:
            return "No description available"

        # Use first line as short description
        first_line = doc.strip().splitlines()[0]
        return first_line

    def _parse_docstring_params(self, func) -> dict[str, str]:
        """
        Extract parameter descriptions from a function's docstring.
        Supports common docstring styles: Google, NumPy, Sphinx.

        Args:
            func: Python function object

        Returns:
            Mapping of parameter name -> description text
        """
        docstring = inspect.getdoc(func)
        if not docstring:
            return {}

        param_descriptions = {}

        # Google Style: "Args:" or "Arguments:"
        # Example:
        # Args:
        #     path: file path.
        #     start_line: starting line number (1-based), default 1.
        google_match = re.search(r"(?:Args?|Arguments?):\s*\n((?:[ \t]+\w+.*\n?)*)", docstring, re.MULTILINE)
        if google_match:
            args_block = google_match.group(1)
            # Split parameter block: each parameter starts with "indent + param_name:"
            current_param = None
            current_desc_lines = []

            for line in args_block.split("\n"):
                # Check if this is a parameter definition line (indent + param_name + colon)
                param_match = re.match(r"^[ \t]+(\w+)\s*:\s*(.*)$", line)
                if param_match:
                    # Save the previous parameter
                    if current_param:
                        param_descriptions[current_param] = " ".join(current_desc_lines).strip()
                    # Start new parameter
                    current_param = param_match.group(1)
                    current_desc_lines = [param_match.group(2)] if param_match.group(2) else []
                elif current_param and line.strip():
                    # Continue description (deeper indentation)
                    current_desc_lines.append(line.strip())

            # Save the last parameter
            if current_param:
                param_descriptions[current_param] = " ".join(current_desc_lines).strip()

            return param_descriptions

        # NumPy Style: "Parameters" or "Parameters\n----------"
        # Example:
        # Parameters
        # ----------
        # path : str
        #     file path
        numpy_match = re.search(
            r"Parameters?\s*\n\s*-{3,}\s*\n((?:.*\n?)*?)(?=\n\s*(?:Returns?|Yields?|Raises?|Examples?|Notes?|See Also|References)\s*\n|\Z)",
            docstring,
            re.MULTILINE,
        )
        if numpy_match:
            params_block = numpy_match.group(1)
            # Match parameter block: param name (possibly with type) + indented description
            for match in re.finditer(r"^(\w+)\s*:.*?\n((?:[ \t]+.+\n?)*)", params_block, re.MULTILINE):
                param_name = match.group(1).strip()
                description = match.group(2).strip()
                # Clean up extra whitespace
                description = re.sub(r"\s+", " ", description)
                param_descriptions[param_name] = description
            return param_descriptions

        # Sphinx Style: ":param name: description" or ":type name: type"
        # Example:
        # :param path: file path
        # :type path: str
        sphinx_params = re.findall(r":param\s+(\w+)\s*:\s*(.+?)(?=\n|$)", docstring)
        if sphinx_params:
            for param_name, description in sphinx_params:
                param_descriptions[param_name.strip()] = description.strip()
            return param_descriptions

        return {}

    def _extract_parameters_schema(self, func) -> dict:
        """
        Extract JSON Schema from function signature

        Args:
            func: Python function object

        Returns:
            JSON Schema object for function parameters
        """
        from typing import get_type_hints

        sig = inspect.signature(func)
        properties = {}
        required = []

        # Get type annotations
        try:
            hints = get_type_hints(func)
        except Exception:
            hints = {}

        # Extract parameter descriptions from docstring
        param_descriptions = self._parse_docstring_params(func)

        for param_name, param in sig.parameters.items():
            # Skip self and cls
            if param_name in ["self", "cls"]:
                continue

            # Extract parameter type
            param_type = hints.get(param_name, param.annotation)
            if param_type == inspect.Parameter.empty:
                # No type annotation, default to string
                param_schema = {"type": "string"}
            else:
                param_schema = self._python_type_to_json_schema(param_type)

            # Add parameter description (prefer docstring description, fallback to default)
            param_schema["description"] = param_descriptions.get(param_name, f"Parameter {param_name}")

            properties[param_name] = param_schema

            # Check if required (parameters without default values)
            if param.default == inspect.Parameter.empty:
                required.append(param_name)

        return {"type": "object", "properties": properties, "required": required}

    def _python_type_to_json_schema(self, py_type) -> dict:
        """
        Convert Python type annotation to JSON Schema type

        Args:
            py_type: Python type annotation

        Returns:
            JSON Schema type definition
        """
        from typing import get_args, get_origin

        # Basic type mapping
        if py_type is str:
            return {"type": "string"}
        elif py_type is int:
            return {"type": "integer"}
        elif py_type is float:
            return {"type": "number"}
        elif py_type is bool:
            return {"type": "boolean"}

        # Complex types (List, Dict, Optional, etc.)
        origin = get_origin(py_type)

        # List type
        if origin is list:
            args = get_args(py_type)
            if args:
                item_schema = self._python_type_to_json_schema(args[0])
                return {"type": "array", "items": item_schema}
            else:
                return {"type": "array", "items": {"type": "string"}}

        # Dict type
        elif origin is dict:
            return {"type": "object"}

        # Optional type (Union[X, None])
        elif origin is Union:
            args = get_args(py_type)
            # Filter out NoneType
            non_none_args = [arg for arg in args if arg is not type(None)]
            if non_none_args:
                return self._python_type_to_json_schema(non_none_args[0])

        # Any or other unknown types, default to string
        return {"type": "string"}

    def _generate_single_tool_desc(self, namespace: str, tool_set: object, detail: bool) -> str:
        lines = []
        doc = inspect.getdoc(tool_set) or ""
        if detail:
            lines.append(f"### Tool set: `{namespace}`")
            if doc:
                lines.append(f"  **Description**: {doc.strip()}")
            seen_funcs = set()  # Dedup: skip aliases (multiple names pointing to the same function object)
            for name, func in inspect.getmembers(tool_set, inspect.isfunction):
                if name.startswith("_"):
                    continue
                if id(func) in seen_funcs:
                    continue
                seen_funcs.add(id(func))
                try:
                    sig = inspect.signature(func)
                    f_doc = inspect.getdoc(func) or "No description"
                    lines.append(f"\n  **Tool: `{namespace}.{name}{sig}`**")
                    lines.append(f"    - **Description**: {f_doc.splitlines()[0]}")
                except (ValueError, TypeError):
                    pass
            lines.append("-" * 20)
        else:
            summary = doc.splitlines()[0] if doc else "Extended tool set"
            # List function names so the agent knows the tool set's capabilities
            func_names = []
            seen_funcs = set()
            for name, func in inspect.getmembers(tool_set, inspect.isfunction):
                if name.startswith("_"):
                    continue
                if id(func) in seen_funcs:
                    continue
                seen_funcs.add(id(func))
                func_names.append(name)
            funcs_str = ", ".join(f"`{n}`" for n in func_names) if func_names else "None"
            lines.append(
                f"- **`{namespace}`**: {summary}. Contains: {funcs_str} (call `help.get_tool_help(namespace='{namespace}')` for details)"
            )
        return "\n".join(lines)

    def _generate_mcp_desc(self, detail: bool, filter_server: str | None = None) -> str:
        lines = []
        if detail:
            lines.append("### MCP External Tool Set (detailed)")
            for tool in self._mcp_adapter.get_all_tools():
                f = tool["function"]
                if filter_server and not f["name"].startswith(filter_server):
                    continue
                lines.append(f"\n  **Tool: `{f['name']}`**")
                lines.append(f"    - **Description**: {f.get('description', 'None')}")
                lines.append(f"    - **Parameters**: {json.dumps(f.get('parameters', {}), ensure_ascii=False)}")
            lines.append("-" * 20)
        else:
            # Dynamically list all connected MCP server names
            server_names = (
                list(self._mcp_adapter._tools_cache.keys()) if hasattr(self._mcp_adapter, "_tools_cache") else []
            )
            servers_str = ", ".join(server_names) if server_names else "None"
            lines.append(
                f"- **`mcp_tools`**: External MCP service integration. Available servers: {servers_str}. Call `help.get_tool_help(namespace='mcp__<server_name>')` for details"
            )
        return "\n".join(lines)

    async def call(self, tool_name: str, args: str | dict[str, Any]) -> Any:
        """
        Dispatch a tool call

        Args:
            tool_name: full tool name (namespace.function)
            args: parameter dict (new format) or JSON string (legacy format compatibility)
        """
        tc_log = get_tool_call_debug_logger()

        # Compatibility: if a JSON string is passed, parse it automatically
        if isinstance(args, str):
            args_preview = args[:300] if args else ""
            tc_log.info("[registry.call] tool_name=%r, args_json=%s", tool_name, args_preview)
            try:
                args = json.loads(args) if args else {}
            except json.JSONDecodeError:
                tc_log.warning(
                    "[registry.call] Invalid JSON args for tool %r: %s", tool_name, args[:200] if args else ""
                )
                return "Error: Invalid JSON arguments."
        else:
            tc_log.info("[registry.call] tool_name=%r, args_dict=%r", tool_name, args)

        args = args or {}

        if tool_name == "help.get_tool_help":
            return self.get_tool_help(args.get("namespace", ""))

        # Synthetic internal tool — never meant to be called by LLM.
        # Pipeline events are delivered automatically by the runner.
        if tool_name in ("event_pipeline", "system__event_pipeline"):
            return "Note: event_pipeline is an internal mechanism. External events are delivered automatically. No action needed."

        if tool_name.startswith("mcp__") and self._mcp_adapter:
            # Use async MCP call with real asyncio timeout control
            tc_log.debug("[registry.call] Routing to MCP adapter: %r", tool_name)
            with self._lock:
                adapter = self._mcp_adapter
            return await adapter.call_tool_async(tool_name, args)

        # Support both formats: namespace.function (XML) and namespace__function (Native FC)
        if "." in tool_name:
            ns, fn = tool_name.split(".", 1)
        elif "__" in tool_name:
            # Native Function Calling format: namespace__function
            ns, fn = tool_name.split("__", 1)
            tc_log.debug("[registry.call] Converted Native FC format: %s -> %s.%s", tool_name, ns, fn)
        else:
            tc_log.warning(
                "[registry.call] INVALID FORMAT: tool_name=%r has neither '.' nor '__'. Registered namespaces: %s",
                tool_name,
                list(self._tools.keys()),
            )
            return f"Error: Invalid format {tool_name}"

        with self._lock:
            if ns not in self._tools:
                tc_log.warning("[registry.call] Namespace %r not found. Available: %s", ns, list(self._tools.keys()))
                return f"Error: Namespace {ns} not found"

            module_info = self._tools[ns]

        func = getattr(module_info["module"], fn, None)
        if not func:
            tc_log.warning("[registry.call] Function %r not found in namespace %r", fn, ns)
            return f"Error: Function {fn} not found"

        try:
            import asyncio
            import functools
            from typing import get_args, get_origin, get_type_hints

            # Parameter type conversion (XML format compatibility)
            # Read function type annotations, auto-convert strings to target types
            try:
                hints = get_type_hints(func)
                converted_args = {}
                for key, value in args.items():
                    if key in hints:
                        expected_type = hints[key]
                        origin = get_origin(expected_type)

                        # 1. If List[str] expected and a string is passed, auto-split
                        if origin is list:
                            type_args = get_args(expected_type)
                            if type_args and type_args[0] is str and isinstance(value, str):
                                # Separator priority: semicolon > comma
                                # This handles cases where content itself contains commas
                                if ";" in value:
                                    # Use semicolon separator (content may contain commas)
                                    converted_args[key] = [s.strip() for s in value.split(";") if s.strip()]
                                    tc_log.debug(
                                        "[registry.call] Auto-convert %r: str -> List[str] (semicolon, %d items)",
                                        key,
                                        len(converted_args[key]),
                                    )
                                else:
                                    # Use comma separator (normal case)
                                    converted_args[key] = [s.strip() for s in value.split(",") if s.strip()]
                                    tc_log.debug(
                                        "[registry.call] Auto-convert %r: str -> List[str] (comma, %d items)",
                                        key,
                                        len(converted_args[key]),
                                    )
                            else:
                                converted_args[key] = value

                        # 2. If int expected and a string is passed, auto-convert to integer
                        elif expected_type is int and isinstance(value, str):
                            try:
                                converted_args[key] = int(value)
                                tc_log.debug(
                                    "[registry.call] Auto-convert %r: str '%s' -> int %d",
                                    key,
                                    value,
                                    converted_args[key],
                                )
                            except ValueError:
                                tc_log.warning(
                                    "[registry.call] Failed to convert %r='%s' to int, using original value", key, value
                                )
                                converted_args[key] = value

                        # 3. If float expected and a string is passed, auto-convert to float
                        elif expected_type is float and isinstance(value, str):
                            try:
                                converted_args[key] = float(value)
                                tc_log.debug(
                                    "[registry.call] Auto-convert %r: str '%s' -> float %f",
                                    key,
                                    value,
                                    converted_args[key],
                                )
                            except ValueError:
                                tc_log.warning(
                                    "[registry.call] Failed to convert %r='%s' to float, using original value",
                                    key,
                                    value,
                                )
                                converted_args[key] = value

                        # 4. If bool expected and a string is passed, auto-convert to bool
                        elif expected_type is bool and isinstance(value, str):
                            # Support multiple boolean representations
                            if value.lower() in ("true", "1", "yes", "on"):
                                converted_args[key] = True
                                tc_log.debug("[registry.call] Auto-convert %r: str '%s' -> bool True", key, value)
                            elif value.lower() in ("false", "0", "no", "off", ""):
                                converted_args[key] = False
                                tc_log.debug("[registry.call] Auto-convert %r: str '%s' -> bool False", key, value)
                            else:
                                tc_log.warning(
                                    "[registry.call] Ambiguous bool value %r='%s', defaulting to True", key, value
                                )
                                converted_args[key] = True  # Non-empty string defaults to True

                        else:
                            converted_args[key] = value
                    else:
                        converted_args[key] = value
                args = converted_args
            except Exception as type_conv_err:
                tc_log.warning(
                    "[registry.call] Type conversion failed for %s.%s: %s (using original args)", ns, fn, type_conv_err
                )

            if asyncio.iscoroutinefunction(func):
                result = await func(**args)
            else:
                # Synchronous tools must be run in a thread pool to avoid blocking the event loop
                # (some tools internally use time.sleep / subprocess.run and other blocking calls;
                #  running them directly on the event loop thread causes WebSocket heartbeat timeouts)
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, functools.partial(func, **args))
            tc_log.debug("[registry.call] OK: %s.%s -> result_len=%d", ns, fn, len(str(result)))
            return result
        except Exception as e:
            tc_log.warning("[registry.call] Exception in %s.%s: %s", ns, fn, e)
            return f"Error: {e!s}"

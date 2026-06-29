# Troubleshooting Guide

## 📚 Table of Contents

- [ChatPro Account & Password Management](#chatpro-account--password-management)
  - [Password Recovery](#password-recovery)
  - [Account Management](#account-management)
- [Native Function Calling Troubleshooting](#native-function-calling-troubleshooting)
  - [Quick Diagnosis Flow](#quick-diagnosis-flow)
  - [Common Errors and Solutions](#common-errors-and-solutions)
  - [Configuration Errors](#configuration-errors)
  - [Tool Call Failures](#tool-call-failures)
  - [Model Compatibility Issues](#model-compatibility-issues)
  - [Performance Issues](#performance-issues)
  - [Log Analysis Tips](#log-analysis-tips)
  - [Debugging Tools and Methods](#debugging-tools-and-methods)
  - [Contact Support](#contact-support)

---

## ChatPro Account & Password Management

### Password Recovery

If you have forgotten the password for a ChatPro account, you can reset it with the administrator tool.

#### Reset the password with the administrator tool

OpenSquad provides an admin CLI to reset user passwords.

**Prerequisites**:
- Server access
- The email of the user whose password you want to reset

**Steps**:

```bash
# 1. Go to the project root
cd /path/to/opensquad

# 2. List all users (to find the target user's email)
python -m opensquad.admin list_users

# Example output:
# Found 14 users:
#
# ID       Username              Email                           Status     Registered
# ----------------------------------------------------------------------------------------------------
# 100001   coder-001            coder001@ai                    ONLINE     2026-02-12 15:02
# 100003   project-manager      ai-pm@ai                       ONLINE     2026-02-14 05:10
# test-user Test User            test@example.com               OFFLINE    2026-02-07 20:08

# 3. Reset the password for the specified user
python -m opensquad.admin reset_password <email> <new_password>

# Example:
python -m opensquad.admin reset_password test@example.com NewPass123
```

**Successful output**:
```
✓ User password reset successfully
  User ID:   test-user
  Username:  Test User
  Email:     test@example.com
  New password: NewPass123
```

**Notes**:
- The new password must be at least 6 characters long.
- The password takes effect immediately; the user can log in with the new password.
- For security, deliver the new password to the user through a secure channel (encrypted email, private message).
- Recommend the user change their password immediately after logging in.

**Error handling**:

1. **Password too short**:
```bash
$ python -m opensquad.admin reset_password test@example.com 123
Error: new password must be at least 6 characters long
```

2. **User not found**:
```bash
$ python -m opensquad.admin reset_password nonexist@example.com NewPass123
Error: no user found with email 'nonexist@example.com'
```

3. **Database file missing**:
```bash
Error: database file does not exist: /path/to/opensquad/gateway/backend/chat.db
```
→ Check that you are in the correct project directory and that the Gateway service has been initialized.

---

### Account Management

#### View all users

```bash
python -m opensquad.admin list_users
```

Example output:
```
Found 14 users:

ID       Username              Email                           Status     Registered
----------------------------------------------------------------------------------------------------
u1       Alex Developer       alex@example.com               ONLINE     2026-02-07 19:50
100001   coder-001            coder001@ai                    ONLINE     2026-02-12 15:02
100003   project-manager      ai-pm@ai                       ONLINE     2026-02-14 05:10
100004   test-engineer        ai-qa@ai                       OFFLINE    2026-02-14 13:42
```

Fields shown:
- **ID**: unique user identifier
- **Username**: display name
- **Email**: login account (unique)
- **Status**: ONLINE, OFFLINE, BUSY
- **Registered**: account creation time

#### Help

```bash
# Show admin tool help
python -m opensquad.admin help

# or
python -m opensquad.admin --help
```

Output:
```
OpenSquad admin command-line tool

Usage:
    python -m opensquad.admin <command> [options]

Commands:
    reset_password <email> <new_password>   Reset the password of the given user
    list_users                              List all registered users
    help                                    Show this help message

Examples:
    python -m opensquad.admin reset_password user@example.com NewPass123
    python -m opensquad.admin list_users
```

---

## Native Function Calling Troubleshooting

When you hit a problem, follow this flow to locate the cause quickly.

### 1️⃣ Check the configuration file

```bash
# View the current config
cat agents/<your_agent>/config.json

# Validate the JSON format
python -m json.tool agents/<your_agent>/config.json
```

**Checklist**:
- ✅ `tool_call_mode` is one of `"auto"`, `"native"`, `"xml"`
- ✅ `tool_filter` is `"all"`, `"baseline"`, `"high"`, or a custom array
- ✅ JSON is well-formed (commas, quotes, brackets)

---

### 2️⃣ Inspect the logs to see which mode is in use

```bash
# Start the agent and watch the logs
python main.py

# Find the key log line (successful startup)
grep "Using.*ToolCallStrategy" logs/opensquad.log

# Find error logs
grep "ERROR\|WARNING" logs/opensquad.log | tail -20
```

**Key log examples**:
```
[Runner] Using NativeToolCallStrategy with filter=high (97 tools)
[Runner] Using XMLToolCallStrategy (124 tools)
[Runner] Using ToolCallStrategySelector (auto mode)
```

---

### 3️⃣ Test a tool call

Test that tools work with a simple prompt:

```
User input: search for today's news
Expected behavior: the agent calls the websearch.search_web tool
```

**Check the tool call logs**:
```bash
# Find tool call logs
grep "\[registry.call\]" logs/opensquad.log | tail -10

# Find Native FC parse logs
grep "Native FC parsed" logs/opensquad.log | tail -10
```

---

### 4️⃣ Cross-mode comparison test

If you suspect Native FC is broken, switch to XML mode for comparison:

```json
{
  "model": {
    "tool_call_mode": "xml",  // temporarily switch to XML
    "tool_filter": "high"
  }
}
```

**Restart the agent and test the same prompt**, then compare results:
- If XML mode works → Native FC implementation issue, or the model does not support it
- If XML also fails → base tool registration or some other underlying issue

---

## Common Errors and Solutions

### Configuration Errors

#### ❌ Error 1: `Unknown tool_call_mode`

**Error log**:
```
[WARNING] Unknown tool_call_mode: nativefc. Falling back to XML.
```

**Cause**: the `tool_call_mode` value is misspelled.

**Solution**:
```json
// ❌ Wrong
{"tool_call_mode": "nativefc"}  // wrong
{"tool_call_mode": "Native"}    // wrong case

// ✅ Correct
{"tool_call_mode": "native"}
{"tool_call_mode": "xml"}
{"tool_call_mode": "auto"}
```

---

#### ❌ Error 2: `Unknown tool_filter`

**Error log**:
```
[WARNING] Unknown tool_filter: high-priority, using 'all'
```

**Cause**: the `tool_filter` value is invalid.

**Solution**:
```json
// ❌ Wrong
{"tool_filter": "high-priority"}    // not supported
{"tool_filter": "medium"}            // does not exist
{"tool_filter": ["all"]}             // should not be an array

// ✅ Correct
{"tool_filter": "high"}              // preset mode
{"tool_filter": "baseline"}
{"tool_filter": ["filesystem", "git", "websearch"]}  // custom array
```

---

#### ❌ Error 3: JSON format error

**Symptom**: the agent fails to start, or falls back to default config.

**Common format mistakes**:
```json
// ❌ Mistake 1: trailing comma
{
  "model": {
    "tool_call_mode": "native",  // no comma on the last item
  }
}

// ❌ Mistake 2: missing quotes
{
  "model": {
    tool_call_mode: "native"  // key is not quoted
  }
}

// ❌ Mistake 3: full-width (CJK) quotes/colon
{
  "model": {
    "tool_call_mode"："native"  // full-width colon and quotes
  }
}

// ✅ Correct format
{
  "model": {
    "tool_call_mode": "native",
    "tool_filter": "high"
  }
}
```

**How to validate**:
```bash
# Validate JSON format with Python
python -m json.tool agents/ultimate/config.json

# If valid, it prints pretty-printed JSON.
# If invalid, it shows the exact error location.
```

---

### Tool Call Failures

#### ❌ Error 4: `Invalid format xxx`

**Error log**:
```
[WARNING] [registry.call] INVALID FORMAT: tool_name='search' has neither '.' nor '__'
Error: Invalid format search
```

**Cause**: the tool name is missing its namespace prefix.

**Diagnosis**:
```bash
# View the full error log
grep "INVALID FORMAT" logs/opensquad.log

# View available namespaces
grep "Registered namespaces" logs/opensquad.log
```

**Possible causes**:
1. **The model emitted a malformed name**: it should return `websearch__search` or `websearch.search`, but returned `search`.
2. **The tool was not registered correctly**: the tool set was not loaded.

**Solution**:
- If this happens often, the model may be unsuitable for Native FC — switch to XML mode.
- Check whether the tool is within the `tool_filter` scope.

---

#### ❌ Error 5: `Namespace xxx not found`

**Error log**:
```
[WARNING] [registry.call] Namespace 'webfetch' not found. Available: ['filesystem', 'system', ...]
Error: Namespace webfetch not found
```

**Cause**: the tool namespace does not exist or was not loaded.

**Diagnosis**:
```bash
# View available namespaces
grep "Available:" logs/opensquad.log | tail -1

# or run the diagnostic script
python diagnose_tools.py
```

**Possible causes**:
1. **Tool name typo**: the correct name is `websearch`, not `webfetch`.
2. **The tool was filtered out**: in `"baseline"` mode, some tools are unavailable.

**Solution**:
```json
// Option 1: use the correct tool name
// check the available tools list

// Option 2: adjust the filter
{
  "model": {
    "tool_call_mode": "native",
    "tool_filter": "all"  // include all tools
  }
}

// Option 3: add the needed tools to a custom filter
{
  "model": {
    "tool_filter": ["filesystem", "websearch", "git", "system"]
  }
}
```

---

#### ❌ Error 6: `Function xxx not found`

**Error log**:
```
[WARNING] [registry.call] Function 'search_web_advanced' not found in namespace 'websearch'
Error: Function search_web_advanced not found
```

**Cause**: the namespace exists, but the function name is wrong.

**Diagnosis**:
```python
# View the functions in a namespace
python diagnose_tools.py | grep -A 10 "websearch"

# or with an interactive Python session
python
>>> from opensquad.registry import ToolRegistry
>>> registry = ToolRegistry()
>>> import inspect
>>> tool_set = registry._tools['websearch']['module']
>>> [name for name, func in inspect.getmembers(tool_set, inspect.isfunction) if not name.startswith('_')]
```

**Solution**:
- Check the available function list and use the correct function name.
- If the model is generating wrong names, you may need to refine the prompt or switch to XML mode.

---

#### ❌ Error 7: `Failed to parse tool call arguments`

**Error log**:
```
[ERROR] Failed to parse tool call arguments: Expecting property name enclosed in double quotes
Raw arguments: {query: "test"}
```

**Cause**: the model returned malformed JSON arguments.

**Common format problems**:
```json
// ❌ Wrong
{query: "test"}              // key not quoted
{'query': 'test'}            // single quotes
{query: test}                // value not quoted
{"query": undefined}         // JavaScript syntax

// ✅ Correct
{"query": "test"}
```

**Solution**:
1. **Short term**: switch to XML mode (more forgiving parser).
2. **Long term**:
   - Check whether the model is suitable for Native FC (GPT-4, Claude 3.5, GLM-5 recommended).
   - Tune model parameters (lower temperature).
   - Improve the system prompt.

---

### Model Compatibility Issues

#### ❌ Error 8: Low tool-call rate in Native FC mode

**Symptoms**:
- The agent rarely calls tools.
- It returns plain text when it should have called a tool.
- `[registry.call]` rarely appears in the logs.

**Diagnosis**:
```bash
# Count tool calls
grep -c "\[registry.call\]" logs/opensquad.log

# Compare against the call count in XML mode (after switching config)
```

**Possible causes**:
1. **The model does not support Native FC**: some models do not implement it.
2. **The API does not support it**: the API version in use is too old.
3. **The `tools` parameter is not passed correctly**: an integration issue.

**Solution**:

**Step 1: verify model support**
```python
# View the supported-models list
cat docs/configuration_reference.md | grep -A 20 "models that support Native FC"
```

**Step 2: check the API call logs**
```bash
# See whether API requests include the tools parameter
grep "tools=" logs/opensquad.log | head -5
```

**Step 3: switch to auto mode**
```json
{
  "model": {
    "tool_call_mode": "auto"  // auto-select the best mode
  }
}
```

---

#### ❌ Error 9: API returns an error

**Error log**:
```
[ERROR] API Error: Model does not support function calling
```

**Cause**: the model or API does not support Native FC.

**Solution**:
```json
// Option 1: switch to auto mode (recommended)
{
  "model": {
    "tool_call_mode": "auto"
  }
}

// Option 2: force XML mode
{
  "model": {
    "tool_call_mode": "xml"
  }
}
```

---

### Performance Issues

#### ⚠️ Problem 10: Slow response

**Symptoms**:
- Agent response time > 5 seconds.
- High tool-call latency.

**Diagnosis**:
```bash
# View the tool count
grep "Using.*ToolCallStrategy" logs/opensquad.log | tail -1

# Example output:
# Using NativeToolCallStrategy with filter=all (124 tools)      ← too many
# Using NativeToolCallStrategy with filter=high (97 tools)      ← recommended
# Using NativeToolCallStrategy with filter=baseline (57 tools)  ← fastest
```

**Solution**:

**Option 1: tighten the filter (recommended)**
```json
{
  "model": {
    "tool_call_mode": "native",
    "tool_filter": "baseline"  // 54% fewer tools
  }
}
```

**Option 2: define a minimal custom tool set**
```json
{
  "model": {
    "tool_filter": [
      "filesystem",  // file operations
      "system",      // system commands
      "websearch"    // web search
    ]
  }
}
```

**Option 3: monitor performance**
```bash
# Time a response
time echo "search for today's news" | python main.py

# Compare performance across configs
```

---

#### ⚠️ Problem 11: High token consumption

**Symptoms**:
- Rising API cost.
- Hitting token limits.

**Diagnosis**:
```bash
# View the system prompt length (rough estimate)
grep "System prompt length" logs/opensquad.log

# View the tool count
grep "with filter=" logs/opensquad.log | tail -1
```

**Token consumption comparison**:
| Config | System prompt tokens | Tools | Saving |
|------|---------------------|----------|------|
| XML mode | ~15,000 | 124 | baseline |
| Native FC (all) | ~3,100 | 124 | -79% |
| Native FC (high) | ~2,400 | 97 | -84% |
| Native FC (baseline) | ~1,400 | 57 | -91% |

**Solution**:
```json
{
  "model": {
    "tool_call_mode": "native",     // -79% tokens
    "tool_filter": "baseline"        // another -50%
  }
}
```

---

#### ⚠️ Problem 12: High error rate

**Symptoms**:
- Frequent `Invalid format` errors.
- Tool-call argument parsing failures.
- The agent produces incorrect results.

**Diagnosis**:
```bash
# Count error log lines
grep "ERROR\|WARNING.*Invalid\|Failed to parse" logs/opensquad.log | wc -l

# View the error-type distribution
grep "ERROR" logs/opensquad.log | cut -d: -f3 | sort | uniq -c | sort -rn
```

**Solution**:

**If the error rate is > 10%**:
```json
{
  "model": {
    "tool_call_mode": "xml"  // fall back to XML mode
  }
}
```

**If errors are occasional (< 5%)**:
- Normal — Native FC has an inherent ~5% error rate.
- Can be mitigated with a retry mechanism.

**If you are on GLM-5 and the error rate is high**:
- See `GLM5_ARG_VALUE_FIX.md` and `GLM5_PATCHES.md`.
- Apply the relevant patches.

---

## Log Analysis Tips

### Key log locations

**1. Strategy selection log** (at startup)
```bash
grep "Using.*ToolCallStrategy" logs/opensquad.log | tail -1
```

Example output:
```
[Runner] Using NativeToolCallStrategy with filter=high (97 tools)
```

---

**2. Tool call log** (per call)
```bash
grep "\[registry.call\]" logs/opensquad.log | tail -10
```

Example output:
```
[registry.call] tool_name='websearch__search', args_dict={'query': "today's news"}
[registry.call] Converted Native FC format: websearch__search → websearch.search
```

---

**3. Native FC parse log** (Native mode only)
```bash
grep "Native FC parsed" logs/opensquad.log | tail -10
```

Example output:
```
Native FC parsed tool call: websearch__search
```

---

**4. Error logs**
```bash
# All errors and warnings
grep "ERROR\|WARNING" logs/opensquad.log | tail -20

# Tool-call-related errors
grep "registry.call.*WARNING\|ERROR" logs/opensquad.log

# API errors
grep "API Error" logs/opensquad.log
```

---

### Determine which mode is in use

**Method 1: read the strategy selection log**
```bash
grep "Using.*ToolCallStrategy" logs/opensquad.log | tail -1
```

Output meaning:
- `NativeToolCallStrategy` → Native FC mode
- `XMLToolCallStrategy` → XML mode
- `ToolCallStrategySelector` → Auto mode (will pick one afterwards)

---

**Method 2: read the tool call format**
```bash
grep "tool_name=" logs/opensquad.log | tail -5
```

Example output:
- `tool_name='websearch__search'` → Native FC format
- `tool_name='websearch.search'` → XML format

---

**Method 3: check Native FC-specific logs**
```bash
grep "Native FC parsed" logs/opensquad.log | tail -1
```

- If there is output → Native FC is in use.
- If there is no output → XML is in use, or no tool was called.

---

### Trace a full tool-call flow

**Example: tracing a "search for today's news" request**

```bash
# 1. View user input
grep "User:" logs/opensquad.log | tail -5

# 2. View the API call
grep "LLM API call" logs/opensquad.log | tail -1

# 3. View tool parsing
grep "Native FC parsed\|Parsed XML tool call" logs/opensquad.log | tail -1

# 4. View tool execution
grep "\[registry.call\].*websearch" logs/opensquad.log | tail -1

# 5. View the tool result
grep "Tool result:" logs/opensquad.log | tail -1

# 6. View the final response
grep "Assistant:" logs/opensquad.log | tail -1
```

---

### Adjust the log level

**Temporary (this run only)**
```bash
# Set the env var at startup
export LOG_LEVEL=DEBUG
python main.py

# Windows
set LOG_LEVEL=DEBUG
python main.py
```

**Permanent (edit config)**
```python
# Edit opensquad/logging_config.py
import logging

# Change the default level
logging.basicConfig(level=logging.DEBUG)  # set to DEBUG
```

**Log levels**:
- `DEBUG`: most verbose, all debug info
- `INFO`: normal info (recommended, default)
- `WARNING`: warnings and errors
- `ERROR`: errors only

---

## Debugging Tools and Methods

### 1. Tool diagnostic script

**Run the diagnostic**:
```bash
python diagnose_tools.py
```

**Example output**:
```
=== Tool Registry Diagnostics ===

Registered namespaces (10):
- filesystem (11 functions)
- system (8 functions)
- websearch (3 functions)
- git (16 functions)
...

Total tools: 124

Tool filter modes:
- all: 124 tools (0% reduction)
- high: 97 tools (-22% reduction)
- baseline: 57 tools (-54% reduction)

Testing tool call formats:
✅ websearch.search → OK
✅ websearch__search → OK
❌ search → FAILED (Invalid format)
```

---

### 2. Tool usage analysis script

**Run the analysis**:
```bash
python analyze_tool_usage.py logs/opensquad.log
```

**Example output**:
```
=== Tool Usage Analysis ===

Top 10 most used tools:
1. websearch.search: 45 calls
2. filesystem.read_file: 32 calls
3. system.run_command: 28 calls
...

Error rate: 12/200 calls (6%)
Average response time: 1.2s

Recommendations:
- Error rate is acceptable (< 10%)
- Consider using 'high' filter (3 unused tools in 'all' mode)
```

---

### 3. Interactive testing

**Start a Python REPL to test tool calls**:
```python
python
>>> from opensquad.registry import ToolRegistry
>>> import asyncio
>>>
>>> # Initialize the registry
>>> registry = ToolRegistry()
>>>
>>> # Test a tool call (XML format)
>>> result = asyncio.run(registry.call("websearch.search", {"query": "test"}))
>>> print(result)
>>>
>>> # Test a tool call (Native FC format)
>>> result = asyncio.run(registry.call("websearch__search", {"query": "test"}))
>>> print(result)
>>>
>>> # List available tools
>>> print(list(registry._tools.keys()))
>>>
>>> # List tool functions
>>> import inspect
>>> tool_set = registry._tools['websearch']['module']
>>> funcs = [name for name, func in inspect.getmembers(tool_set, inspect.isfunction) if not name.startswith('_')]
>>> print(funcs)
```

---

### 4. Comparison test method

**Create a test script `test_modes.py`**:
```python
import json
import time
import subprocess

# Test configs
configs = [
    {"tool_call_mode": "xml", "tool_filter": "all"},
    {"tool_call_mode": "native", "tool_filter": "all"},
    {"tool_call_mode": "native", "tool_filter": "high"},
    {"tool_call_mode": "native", "tool_filter": "baseline"},
]

test_queries = [
    "search for today's news",
    "list files in the current directory",
    "show git status",
]

for config in configs:
    print(f"\n=== Testing: {config} ===")

    # Write the config
    with open("agents/ultimate/config.json", "r") as f:
        full_config = json.load(f)
    full_config["model"].update(config)
    with open("agents/ultimate/config.json", "w") as f:
        json.dump(full_config, f, indent=2)

    # Run the test
    for query in test_queries:
        start = time.time()
        result = subprocess.run(
            ["python", "main.py"],
            input=query,
            text=True,
            capture_output=True,
            timeout=30
        )
        elapsed = time.time() - start
        print(f"  Query: {query}")
        print(f"  Time: {elapsed:.2f}s")
        print(f"  Success: {'✅' if result.returncode == 0 else '❌'}")
```

**Run the comparison**:
```bash
python test_modes.py > comparison_results.txt
```

---

### 5. Log filter

**Create a reusable log filter `log_filter.sh`**:
```bash
#!/bin/bash

LOG_FILE="logs/opensquad.log"

case "$1" in
  "strategy")
    grep "Using.*ToolCallStrategy" "$LOG_FILE" | tail -20
    ;;
  "calls")
    grep "\[registry.call\]" "$LOG_FILE" | tail -20
    ;;
  "errors")
    grep "ERROR\|WARNING" "$LOG_FILE" | tail -20
    ;;
  "native")
    grep "Native FC" "$LOG_FILE" | tail -20
    ;;
  "tools")
    grep "tool_name=" "$LOG_FILE" | cut -d"'" -f2 | sort | uniq -c | sort -rn
    ;;
  *)
    echo "Usage: $0 {strategy|calls|errors|native|tools}"
    ;;
esac
```

**Usage**:
```bash
chmod +x log_filter.sh

./log_filter.sh strategy  # strategy selection
./log_filter.sh calls     # tool calls
./log_filter.sh errors    # errors
./log_filter.sh native    # Native FC logs
./log_filter.sh tools     # tool usage frequency
```

---

## Contact Support

If the above steps do not resolve your issue, gather the following information and contact the dev team.

### 📋 Information checklist

**1. Environment info**
```bash
# Python version
python --version

# Dependency versions
pip list | grep -E "(openai|anthropic|zhipuai)"

# System info
uname -a  # Linux/Mac
systeminfo | findstr /B /C:"OS Name" /C:"OS Version"  # Windows
```

**2. Configuration file**
```bash
# Full config (with sensitive values removed)
cat agents/<your_agent>/config.json | grep -v "api_key"
```

**3. Log files**
```bash
# Last 100 log lines
tail -100 logs/opensquad.log > debug_logs.txt

# or just error logs
grep "ERROR\|WARNING" logs/opensquad.log > error_logs.txt
```

**4. Problem description**
- Symptom (screenshot or text)
- Reproduction steps (how to trigger it)
- Expected vs. actual behavior
- Whether you ran a comparison test (XML vs. Native FC)

---

### 📨 Filing an issue

**GitHub Issues** (recommended):
1. Go to the GitHub repository.
2. Click "Issues" → "New Issue".
3. Fill in the issue using the template.
4. Attach the log files and config (with sensitive info removed).

**Issue title format**:
```
[Native FC] tool call failure - Invalid format error
[Native FC] config error - Unknown tool_call_mode
[Native FC] performance issue - slow response
```

---

### 🔍 Self-service troubleshooting

Before filing an issue, please try:

**✅ Basic checks**:
1. Restart the agent (clears caches).
2. Validate the JSON config format.
3. Check the log files (ERROR/WARNING).
4. Run the diagnostic script.

**✅ Comparison tests**:
1. Switch to XML mode and test.
2. Switch to `auto` mode and test.
3. Adjust `tool_filter` and test.

**✅ Read the docs**:
1. [Configuration reference](configuration_reference.md)
2. [Agent management guide](agent_management.md)

---

### 📚 Related resources

**Code references**:
- Strategy implementation: `opensquad/tool_call_strategy.py`
- Tool registry: `opensquad/registry.py`
- Runner integration: `opensquad/runner.py`

---

## 🎯 Quick Reference Card

### Common config problems cheat sheet

| Symptom | Likely cause | Quick fix |
|------|----------|----------|
| Agent uses XML mode | `tool_call_mode` typo | Check the config, set it to `"native"` or `"auto"` |
| Tool calls fail | `tool_filter` excluded the needed tool | Use `"all"` or a custom array |
| Slow response | Too many tools (124) | Use `"baseline"` or `"high"` |
| High error rate | Model does not support Native FC | Use `"auto"` or `"xml"` |
| API error | API does not support function calling | Use `"xml"` mode |
| JSON format error | Config file is malformed | Validate with `python -m json.tool config.json` |

---

### Log keyword cheat sheet

| Keyword | Meaning | Command |
|--------|------|----------|
| `Using NativeToolCallStrategy` | Native FC enabled | `grep "Using.*Native" logs/opensquad.log` |
| `Using XMLToolCallStrategy` | XML mode enabled | `grep "Using.*XML" logs/opensquad.log` |
| `Native FC parsed` | Native FC parsed successfully | `grep "Native FC parsed" logs/opensquad.log` |
| `[registry.call]` | Tool call executed | `grep "\[registry.call\]" logs/opensquad.log` |
| `INVALID FORMAT` | Tool name format error | `grep "INVALID FORMAT" logs/opensquad.log` |
| `Namespace .* not found` | Namespace does not exist | `grep "not found" logs/opensquad.log` |
| `Failed to parse` | JSON parse failure | `grep "Failed to parse" logs/opensquad.log` |

---

### Diagnostic command cheat sheet

```bash
# View the current mode
grep "Using.*ToolCallStrategy" logs/opensquad.log | tail -1

# View the tool count
grep "with filter=" logs/opensquad.log | tail -1

# Count tool calls
grep -c "\[registry.call\]" logs/opensquad.log

# Count errors
grep -c "ERROR\|WARNING.*Invalid" logs/opensquad.log

# View recent errors
grep "ERROR\|WARNING" logs/opensquad.log | tail -20

# Validate JSON format
python -m json.tool agents/<your_agent>/config.json

# Run the diagnostic script
python diagnose_tools.py

# View available tools
python diagnose_tools.py | grep "Registered namespaces" -A 50
```

---

**Last updated**: 2026-03-01
**Version**: 1.0
**Maintained by**: OpenSquad dev team

Issues and pull requests welcome!

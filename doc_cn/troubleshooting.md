# 故障排查手册

## 📚 目录

- [ChatPro 账号密码管理](#chatpro-账号密码管理)
  - [密码找回](#密码找回)
  - [账号管理](#账号管理)
- [Native Function Calling 故障排查](#native-function-calling-故障排查)
  - [快速诊断流程](#快速诊断流程)
  - [常见错误及解决方案](#常见错误及解决方案)
  - [配置错误](#配置错误)
  - [工具调用失败](#工具调用失败)
  - [模型兼容性问题](#模型兼容性问题)
  - [性能问题](#性能问题)
  - [日志分析技巧](#日志分析技巧)
  - [调试工具和方法](#调试工具和方法)
  - [联系支持](#联系支持)

  - [联系支持](#联系支持)

---

## ChatPro 账号密码管理

### 密码找回

如果忘记了 ChatPro 账号的密码，可以通过管理员工具重置密码。

#### 使用管理员工具重置密码

OpenSquad 提供了管理员命令行工具来重置用户密码。

**前提条件**：
- 需要有服务器访问权限
- 知道需要重置密码的用户邮箱

**重置步骤**：

```bash
# 1. 进入项目根目录
cd /path/to/opensquad

# 2. 列出所有用户（找到目标用户的邮箱）
python -m opensquad.admin list_users

# 示例输出：
# 共找到 14 个用户:
#
# ID       用户名                  邮箱                             状态         注册时间
# ----------------------------------------------------------------------------------------------------
# 100001   coder-001            coder001@ai                    ONLINE     2026-02-12 15:02
# 100003   项目经理                 ai-pm@ai                       ONLINE     2026-02-14 05:10
# test-user Test User            test@example.com               OFFLINE    2026-02-07 20:08

# 3. 重置指定用户的密码
python -m opensquad.admin reset_password <邮箱> <新密码>

# 示例：
python -m opensquad.admin reset_password test@example.com NewPass123
```

**成功输出示例**：
```
✓ 成功重置用户密码
  用户ID: test-user
  用户名: Test User
  邮箱: test@example.com
  新密码: NewPass123
```

**注意事项**：
- 新密码长度不能少于 6 个字符
- 密码会立即生效，用户可以使用新密码登录
- 出于安全考虑，请通过安全渠道（如加密邮件、私信）告知用户新密码
- 建议用户登录后立即修改密码

**错误处理**：

1. **密码太短**：
```bash
$ python -m opensquad.admin reset_password test@example.com 123
错误：新密码长度不能少于 6 个字符
```

2. **用户不存在**：
```bash
$ python -m opensquad.admin reset_password nonexist@example.com NewPass123
错误：未找到邮箱为 'nonexist@example.com' 的用户
```

3. **数据库文件不存在**：
```bash
错误：数据库文件不存在: /path/to/opensquad/gateway/backend/chat.db
```
→ 检查是否在正确的项目目录，Gateway 服务是否已初始化

---

### 账号管理

#### 查看所有用户

```bash
python -m opensquad.admin list_users
```

输出示例：
```
共找到 14 个用户:

ID       用户名                  邮箱                             状态         注册时间
----------------------------------------------------------------------------------------------------
u1       Alex Developer       alex@example.com               ONLINE     2026-02-07 19:50
100001   coder-001            coder001@ai                    ONLINE     2026-02-12 15:02
100003   项目经理                 ai-pm@ai                       ONLINE     2026-02-14 05:10
100004   测试工程师                ai-qa@ai                       OFFLINE    2026-02-14 13:42
```

显示信息包括：
- **ID**：用户唯一标识
- **用户名**：显示名称
- **邮箱**：登录账号（唯一）
- **状态**：ONLINE（在线）、OFFLINE（离线）、BUSY（忙碌）
- **注册时间**：账号创建时间

#### 帮助信息

```bash
# 查看管理员工具帮助
python -m opensquad.admin help

# 或
python -m opensquad.admin --help
```

输出：
```
OpenSquad 管理员命令行工具

用法:
    python -m opensquad.admin <command> [options]

命令:
    reset_password <email> <new_password>   重置指定用户的密码
    list_users                              列出所有注册用户
    help                                    显示帮助信息

示例:
    python -m opensquad.admin reset_password user@example.com NewPass123
    python -m opensquad.admin list_users
```

---

## Native Function Calling 故障排查

遇到问题时，按照以下流程快速定位原因：

### 1️⃣ 检查配置文件

```bash
# 查看当前配置
cat agents/<your_agent>/config.json

# 验证 JSON 格式是否正确
python -m json.tool agents/<your_agent>/config.json
```

**检查清单**：
- ✅ `tool_call_mode` 值是否为 `"auto"`, `"native"`, `"xml"` 之一
- ✅ `tool_filter` 值是否为 `"all"`, `"baseline"`, `"high"`, 或自定义数组
- ✅ JSON 格式是否正确（逗号、引号、括号）

---

### 2️⃣ 查看日志判断使用的模式

```bash
# 启动 Agent 并观察日志
python main.py

# 查找关键日志（成功启动）
grep "Using.*ToolCallStrategy" logs/opensquad.log

# 查找错误日志
grep "ERROR\|WARNING" logs/opensquad.log | tail -20
```

**关键日志示例**：
```
[Runner] Using NativeToolCallStrategy with filter=high (97 tools)
[Runner] Using XMLToolCallStrategy (124 tools)
[Runner] Using ToolCallStrategySelector (auto mode)
```

---

### 3️⃣ 测试工具调用

使用简单命令测试工具是否正常：

```
用户输入：搜索一下今天的新闻
预期行为：Agent 调用 websearch.search_web 工具
```

**查看工具调用日志**：
```bash
# 查找工具调用日志
grep "\[registry.call\]" logs/opensquad.log | tail -10

# 查找 Native FC 解析日志
grep "Native FC parsed" logs/opensquad.log | tail -10
```

---

### 4️⃣ 对比模式测试

如果怀疑 Native FC 有问题，切换到 XML 模式对比：

```json
{
  "model": {
    "tool_call_mode": "xml",  // 临时改为 XML
    "tool_filter": "high"
  }
}
```

**重启 Agent 并测试相同命令**，对比结果：
- 如果 XML 模式正常 → Native FC 实现问题或模型不支持
- 如果 XML 也失败 → 基础工具注册或其他问题

---

## 常见错误及解决方案

### 配置错误

#### ❌ 错误 1: `Unknown tool_call_mode`

**错误日志**：
```
[WARNING] Unknown tool_call_mode: nativefc. Falling back to XML.
```

**原因**：`tool_call_mode` 值拼写错误

**解决方案**：
```json
// ❌ 错误写法
{"tool_call_mode": "nativefc"}  // 错误
{"tool_call_mode": "Native"}    // 大小写错误

// ✅ 正确写法
{"tool_call_mode": "native"}
{"tool_call_mode": "xml"}
{"tool_call_mode": "auto"}
```

---

#### ❌ 错误 2: `Unknown tool_filter`

**错误日志**：
```
[WARNING] Unknown tool_filter: high-priority, using 'all'
```

**原因**：`tool_filter` 值不正确

**解决方案**：
```json
// ❌ 错误写法
{"tool_filter": "high-priority"}    // 不支持
{"tool_filter": "medium"}            // 不存在
{"tool_filter": ["all"]}             // 不应该是数组

// ✅ 正确写法
{"tool_filter": "high"}              // 预设模式
{"tool_filter": "baseline"}
{"tool_filter": ["filesystem", "git", "websearch"]}  // 自定义数组
```

---

#### ❌ 错误 3: JSON 格式错误

**错误现象**：Agent 启动失败或使用默认配置

**常见格式错误**：
```json
// ❌ 错误 1: 多余的逗号
{
  "model": {
    "tool_call_mode": "native",  // 最后一项不应有逗号
  }
}

// ❌ 错误 2: 缺少引号
{
  "model": {
    tool_call_mode: "native"  // 键名缺少引号
  }
}

// ❌ 错误 3: 中文引号
{
  "model": {
    "tool_call_mode"："native"  // 使用了中文冒号和引号
  }
}

// ✅ 正确格式
{
  "model": {
    "tool_call_mode": "native",
    "tool_filter": "high"
  }
}
```

**验证方法**：
```bash
# 使用 Python 验证 JSON 格式
python -m json.tool agents/ultimate/config.json

# 如果格式正确，会输出格式化的 JSON
# 如果格式错误，会显示具体错误位置
```

---

### 工具调用失败

#### ❌ 错误 4: `Invalid format xxx`

**错误日志**：
```
[WARNING] [registry.call] INVALID FORMAT: tool_name='search' has neither '.' nor '__'
Error: Invalid format search
```

**原因**：工具名称缺少命名空间前缀

**诊断方法**：
```bash
# 查看完整错误日志
grep "INVALID FORMAT" logs/opensquad.log

# 查看可用的命名空间
grep "Registered namespaces" logs/opensquad.log
```

**可能原因**：
1. **模型生成格式错误**：模型应返回 `websearch__search` 或 `websearch.search`，但返回了 `search`
2. **工具未正确注册**：工具集未加载

**解决方案**：
- 如果频繁发生：可能是模型不适合 Native FC，改用 XML 模式
- 检查工具是否在 `tool_filter` 范围内

---

#### ❌ 错误 5: `Namespace xxx not found`

**错误日志**：
```
[WARNING] [registry.call] Namespace 'webfetch' not found. Available: ['filesystem', 'system', ...]
Error: Namespace webfetch not found
```

**原因**：工具命名空间不存在或未加载

**诊断方法**：
```bash
# 查看可用命名空间
grep "Available:" logs/opensquad.log | tail -1

# 或运行诊断脚本
python diagnose_tools.py
```

**可能原因**：
1. **工具名称拼写错误**：正确的是 `websearch`，不是 `webfetch`
2. **工具被筛选器过滤**：使用 `"baseline"` 模式时，某些工具不可用

**解决方案**：
```json
// 方案 1: 使用正确的工具名称
// 检查可用工具列表

// 方案 2: 调整筛选器
{
  "model": {
    "tool_call_mode": "native",
    "tool_filter": "all"  // 包含所有工具
  }
}

// 方案 3: 自定义筛选器添加需要的工具
{
  "model": {
    "tool_filter": ["filesystem", "websearch", "git", "system"]
  }
}
```

---

#### ❌ 错误 6: `Function xxx not found`

**错误日志**：
```
[WARNING] [registry.call] Function 'search_web_advanced' not found in namespace 'websearch'
Error: Function search_web_advanced not found
```

**原因**：命名空间存在，但函数名称错误

**诊断方法**：
```python
# 查看命名空间包含的函数
python diagnose_tools.py | grep -A 10 "websearch"

# 或使用交互式 Python
python
>>> from opensquad.registry import ToolRegistry
>>> registry = ToolRegistry()
>>> import inspect
>>> tool_set = registry._tools['websearch']['module']
>>> [name for name, func in inspect.getmembers(tool_set, inspect.isfunction) if not name.startswith('_')]
```

**解决方案**：
- 查看可用函数列表，使用正确的函数名
- 如果是模型生成错误，可能需要优化 Prompt 或改用 XML 模式

---

#### ❌ 错误 7: `Failed to parse tool call arguments`

**错误日志**：
```
[ERROR] Failed to parse tool call arguments: Expecting property name enclosed in double quotes
Raw arguments: {query: "test"}
```

**原因**：模型返回的参数 JSON 格式不正确

**常见格式问题**：
```json
// ❌ 错误格式
{query: "test"}              // 键名缺少引号
{'query': 'test'}            // 使用单引号
{query: test}                // 值缺少引号
{"query": undefined}         // JavaScript 语法

// ✅ 正确格式
{"query": "test"}
```

**解决方案**：
1. **短期解决**：改用 XML 模式（更宽容的解析）
2. **长期解决**：
   - 检查模型是否适合 Native FC（推荐 GPT-4、Claude 3.5、GLM-5）
   - 调整模型参数（降低 temperature）
   - 优化 System Prompt

---

### 模型兼容性问题

#### ❌ 错误 8: Native FC 模式下工具调用率低

**症状**：
- Agent 很少调用工具
- 应该调用工具时，只返回文本
- 日志中很少出现 `[registry.call]`

**诊断方法**：
```bash
# 统计工具调用次数
grep -c "\[registry.call\]" logs/opensquad.log

# 对比 XML 模式的调用次数（切换配置后测试）
```

**可能原因**：
1. **模型不支持 Native FC**：某些模型未实现该功能
2. **API 接口不支持**：使用的 API 版本过旧
3. **tools 参数未正确传递**：集成问题

**解决方案**：

**步骤 1：验证模型支持**
```python
# 查看模型支持列表
cat docs/configuration_reference.md | grep -A 20 "支持 Native FC 的模型"
```

**步骤 2：检查 API 调用日志**
```bash
# 查看 API 请求是否包含 tools 参数
grep "tools=" logs/opensquad.log | head -5
```

**步骤 3：改用 auto 模式**
```json
{
  "model": {
    "tool_call_mode": "auto"  // 自动选择最佳模式
  }
}
```

---

#### ❌ 错误 9: API 返回错误

**错误日志**：
```
[ERROR] API Error: Model does not support function calling
```

**原因**：模型或 API 不支持 Native FC

**解决方案**：
```json
// 方案 1: 改用 auto 模式（推荐）
{
  "model": {
    "tool_call_mode": "auto"
  }
}

// 方案 2: 强制使用 XML 模式
{
  "model": {
    "tool_call_mode": "xml"
  }
}
```

---

### 性能问题

#### ⚠️ 问题 10: 响应速度慢

**症状**：
- Agent 响应时间 > 5 秒
- 工具调用延迟高

**诊断方法**：
```bash
# 查看工具数量
grep "Using.*ToolCallStrategy" logs/opensquad.log | tail -1

# 示例输出：
# Using NativeToolCallStrategy with filter=all (124 tools)  ← 过多
# Using NativeToolCallStrategy with filter=high (97 tools)  ← 推荐
# Using NativeToolCallStrategy with filter=baseline (57 tools)  ← 最快
```

**解决方案**：

**方案 1：优化筛选器（推荐）**
```json
{
  "model": {
    "tool_call_mode": "native",
    "tool_filter": "baseline"  // 减少 54% 工具数量
  }
}
```

**方案 2：自定义最小工具集**
```json
{
  "model": {
    "tool_filter": [
      "filesystem",  // 文件操作
      "system",      // 系统命令
      "websearch"    // 网络搜索
    ]
  }
}
```

**方案 3：监控性能**
```bash
# 记录响应时间
time echo "搜索今天的新闻" | python main.py

# 对比不同配置的性能
```

---

#### ⚠️ 问题 11: Token 消耗过高

**症状**：
- API 费用增加
- 达到 Token 限制

**诊断方法**：
```bash
# 查看 System Prompt 长度（粗略估算）
grep "System prompt length" logs/opensquad.log

# 查看工具数量
grep "with filter=" logs/opensquad.log | tail -1
```

**Token 消耗对比**：
| 配置 | System Prompt Token | 工具数量 | 节省 |
|------|---------------------|----------|------|
| XML mode | ~15,000 | 124 | 基线 |
| Native FC (all) | ~3,100 | 124 | -79% |
| Native FC (high) | ~2,400 | 97 | -84% |
| Native FC (baseline) | ~1,400 | 57 | -91% |

**解决方案**：
```json
{
  "model": {
    "tool_call_mode": "native",     // -79% Token
    "tool_filter": "baseline"        // 进一步减少 -50%
  }
}
```

---

#### ⚠️ 问题 12: 错误率高

**症状**：
- 频繁出现 `Invalid format` 错误
- 工具调用参数解析失败
- Agent 执行结果不正确

**诊断方法**：
```bash
# 统计错误日志数量
grep "ERROR\|WARNING.*Invalid\|Failed to parse" logs/opensquad.log | wc -l

# 查看错误类型分布
grep "ERROR" logs/opensquad.log | cut -d: -f3 | sort | uniq -c | sort -rn
```

**解决方案**：

**如果错误率 > 10%**：
```json
{
  "model": {
    "tool_call_mode": "xml"  // 回退到 XML 模式
  }
}
```

**如果只是偶尔错误（< 5%）**：
- 正常现象，Native FC 本身有 ~5% 错误率
- 可以通过重试机制缓解

**如果使用 GLM-5 且错误率高**：
- 参考 `GLM5_ARG_VALUE_FIX.md` 和 `GLM5_PATCHES.md`
- 应用相关补丁

---

## 日志分析技巧

### 关键日志位置

**1. 策略选择日志**（启动时）
```bash
grep "Using.*ToolCallStrategy" logs/opensquad.log | tail -1
```

示例输出：
```
[Runner] Using NativeToolCallStrategy with filter=high (97 tools)
```

---

**2. 工具调用日志**（每次调用）
```bash
grep "\[registry.call\]" logs/opensquad.log | tail -10
```

示例输出：
```
[registry.call] tool_name='websearch__search', args_dict={'query': '今天新闻'}
[registry.call] Converted Native FC format: websearch__search → websearch.search
```

---

**3. Native FC 解析日志**（Native 模式特有）
```bash
grep "Native FC parsed" logs/opensquad.log | tail -10
```

示例输出：
```
Native FC parsed tool call: websearch__search
```

---

**4. 错误日志**
```bash
# 所有错误和警告
grep "ERROR\|WARNING" logs/opensquad.log | tail -20

# 工具调用相关错误
grep "registry.call.*WARNING\|ERROR" logs/opensquad.log

# API 错误
grep "API Error" logs/opensquad.log
```

---

### 判断当前使用的模式

**方法 1：查看策略选择日志**
```bash
grep "Using.*ToolCallStrategy" logs/opensquad.log | tail -1
```

输出含义：
- `NativeToolCallStrategy` → Native FC 模式
- `XMLToolCallStrategy` → XML 模式  
- `ToolCallStrategySelector` → Auto 模式（会在后续选择）

---

**方法 2：查看工具调用格式**
```bash
grep "tool_name=" logs/opensquad.log | tail -5
```

输出示例：
- `tool_name='websearch__search'` → Native FC 格式
- `tool_name='websearch.search'` → XML 格式

---

**方法 3：查看 Native FC 特有日志**
```bash
grep "Native FC parsed" logs/opensquad.log | tail -1
```

- 如果有输出 → 使用 Native FC
- 如果无输出 → 使用 XML 或未调用工具

---

### 追踪完整工具调用流程

**示例：追踪 "搜索今天的新闻" 请求**

```bash
# 1. 查看用户输入
grep "User:" logs/opensquad.log | tail -5

# 2. 查看 API 调用
grep "LLM API call" logs/opensquad.log | tail -1

# 3. 查看工具解析
grep "Native FC parsed\|Parsed XML tool call" logs/opensquad.log | tail -1

# 4. 查看工具执行
grep "\[registry.call\].*websearch" logs/opensquad.log | tail -1

# 5. 查看工具结果
grep "Tool result:" logs/opensquad.log | tail -1

# 6. 查看最终响应
grep "Assistant:" logs/opensquad.log | tail -1
```

---

### 调整日志级别

**临时调整（当次运行）**
```bash
# 启动时设置环境变量
export LOG_LEVEL=DEBUG
python main.py

# Windows
set LOG_LEVEL=DEBUG
python main.py
```

**永久调整（修改配置）**
```python
# 编辑 opensquad/logging_config.py
import logging

# 修改默认级别
logging.basicConfig(level=logging.DEBUG)  # 改为 DEBUG
```

**日志级别说明**：
- `DEBUG`：最详细，包含所有调试信息
- `INFO`：正常信息（推荐，默认）
- `WARNING`：警告和错误
- `ERROR`：仅错误

---

## 调试工具和方法

### 1. 工具诊断脚本

**运行诊断脚本**：
```bash
python diagnose_tools.py
```

**输出示例**：
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

### 2. 工具使用分析脚本

**运行分析脚本**：
```bash
python analyze_tool_usage.py logs/opensquad.log
```

**输出示例**：
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

### 3. 交互式测试

**启动 Python REPL 测试工具调用**：
```python
python
>>> from opensquad.registry import ToolRegistry
>>> import asyncio
>>>
>>> # 初始化 Registry
>>> registry = ToolRegistry()
>>>
>>> # 测试工具调用（XML 格式）
>>> result = asyncio.run(registry.call("websearch.search", {"query": "test"}))
>>> print(result)
>>>
>>> # 测试工具调用（Native FC 格式）
>>> result = asyncio.run(registry.call("websearch__search", {"query": "test"}))
>>> print(result)
>>>
>>> # 查看可用工具
>>> print(list(registry._tools.keys()))
>>>
>>> # 查看工具函数
>>> import inspect
>>> tool_set = registry._tools['websearch']['module']
>>> funcs = [name for name, func in inspect.getmembers(tool_set, inspect.isfunction) if not name.startswith('_')]
>>> print(funcs)
```

---

### 4. 对比测试方法

**创建测试脚本 `test_modes.py`**：
```python
import json
import time
import subprocess

# 测试配置
configs = [
    {"tool_call_mode": "xml", "tool_filter": "all"},
    {"tool_call_mode": "native", "tool_filter": "all"},
    {"tool_call_mode": "native", "tool_filter": "high"},
    {"tool_call_mode": "native", "tool_filter": "baseline"},
]

test_queries = [
    "搜索今天的新闻",
    "列出当前目录的文件",
    "查看 git 状态",
]

for config in configs:
    print(f"\n=== Testing: {config} ===")

    # 写入配置
    with open("agents/ultimate/config.json", "r") as f:
        full_config = json.load(f)
    full_config["model"].update(config)
    with open("agents/ultimate/config.json", "w") as f:
        json.dump(full_config, f, indent=2)

    # 运行测试
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

**运行对比测试**：
```bash
python test_modes.py > comparison_results.txt
```

---

### 5. 日志过滤器

**创建常用日志过滤器 `log_filter.sh`**：
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

**使用方法**：
```bash
chmod +x log_filter.sh

./log_filter.sh strategy  # 查看策略选择
./log_filter.sh calls     # 查看工具调用
./log_filter.sh errors    # 查看错误
./log_filter.sh native    # 查看 Native FC 日志
./log_filter.sh tools     # 统计工具使用频率
```

---

## 联系支持

如果以上方法无法解决问题，请准备以下信息联系开发团队：

### 📋 信息清单

**1. 环境信息**
```bash
# Python 版本
python --version

# 依赖版本
pip list | grep -E "(openai|anthropic|zhipuai)"

# 系统信息
uname -a  # Linux/Mac
systeminfo | findstr /B /C:"OS Name" /C:"OS Version"  # Windows
```

**2. 配置文件**
```bash
# 完整配置（去除敏感信息）
cat agents/<your_agent>/config.json | grep -v "api_key"
```

**3. 日志文件**
```bash
# 最近 100 行日志
tail -100 logs/opensquad.log > debug_logs.txt

# 或者错误日志
grep "ERROR\|WARNING" logs/opensquad.log > error_logs.txt
```

**4. 问题描述**
- 问题现象（截图或文字描述）
- 重现步骤（如何触发问题）
- 预期行为 vs 实际行为
- 是否使用了对比测试（XML vs Native FC）

---

### 📨 提交问题

**GitHub Issues**（推荐）：
1. 访问 GitHub 项目仓库
2. 点击 "Issues" → "New Issue"
3. 使用模板填写问题信息
4. 附上日志文件和配置（去除敏感信息）

**问题标题格式**：
```
[Native FC] 工具调用失败 - Invalid format error
[Native FC] 配置错误 - Unknown tool_call_mode
[Native FC] 性能问题 - 响应速度慢
```

---

### 🔍 自助排查建议

在提交问题前，请尝试：

**✅ 基础排查**：
1. 重启 Agent（清除缓存）
2. 验证 JSON 配置格式
3. 检查日志文件（ERROR/WARNING）
4. 运行诊断脚本

**✅ 对比测试**：
1. 切换到 XML 模式测试
2. 切换到 `auto` 模式测试
3. 调整 `tool_filter` 测试

**✅ 查看文档**：
1. [配置参数详解](configuration_reference.md)
2. [Agent 管理完全指南](agent_management.md)

---

### 📚 相关资源

**官方文档**：
- [配置参数详解](configuration_reference.md)
- [Agent 管理完全指南](agent_management.md)

**代码参考**：
- 策略实现：`opensquad/tool_call_strategy.py`
- 工具注册：`opensquad/registry.py`
- Runner 集成：`opensquad/runner.py`

---

## 🎯 快速参考卡片

### 常见配置问题速查表

| 症状 | 可能原因 | 快速解决 |
|------|----------|----------|
| Agent 使用 XML 模式 | `tool_call_mode` 拼写错误 | 检查配置，改为 `"native"` 或 `"auto"` |
| 工具调用失败 | `tool_filter` 过滤了所需工具 | 改为 `"all"` 或自定义数组 |
| 响应速度慢 | 工具数量过多（124 tools） | 使用 `"baseline"` 或 `"high"` |
| 错误率高 | 模型不支持 Native FC | 改为 `"auto"` 或 `"xml"` |
| API 错误 | API 不支持 function calling | 改为 `"xml"` 模式 |
| JSON 格式错误 | 配置文件格式不正确 | 运行 `python -m json.tool config.json` 验证 |

---

### 日志关键词速查表

| 关键词 | 含义 | 查找命令 |
|--------|------|----------|
| `Using NativeToolCallStrategy` | 启用 Native FC | `grep "Using.*Native" logs/opensquad.log` |
| `Using XMLToolCallStrategy` | 启用 XML 模式 | `grep "Using.*XML" logs/opensquad.log` |
| `Native FC parsed` | Native FC 成功解析 | `grep "Native FC parsed" logs/opensquad.log` |
| `[registry.call]` | 工具调用执行 | `grep "\[registry.call\]" logs/opensquad.log` |
| `INVALID FORMAT` | 工具名称格式错误 | `grep "INVALID FORMAT" logs/opensquad.log` |
| `Namespace .* not found` | 命名空间不存在 | `grep "not found" logs/opensquad.log` |
| `Failed to parse` | JSON 解析失败 | `grep "Failed to parse" logs/opensquad.log` |

---

### 诊断命令速查表

```bash
# 查看当前模式
grep "Using.*ToolCallStrategy" logs/opensquad.log | tail -1

# 查看工具数量
grep "with filter=" logs/opensquad.log | tail -1

# 统计工具调用次数
grep -c "\[registry.call\]" logs/opensquad.log

# 统计错误次数
grep -c "ERROR\|WARNING.*Invalid" logs/opensquad.log

# 查看最近错误
grep "ERROR\|WARNING" logs/opensquad.log | tail -20

# 验证 JSON 格式
python -m json.tool agents/<your_agent>/config.json

# 运行诊断脚本
python diagnose_tools.py

# 查看可用工具
python diagnose_tools.py | grep "Registered namespaces" -A 50
```

---

**更新日期**：2026-03-01  
**版本**：1.0  
**维护者**：OpenSquad 开发团队

如有问题或建议，欢迎提交 Issue 或 Pull Request！

### 2.1 Tool Call Format

You call tools using XML tags. **Multiple independent tools can be called in parallel** — output multiple <tool_call> blocks in one response.

**Standard Structure**:
```xml
<tool_call>
  <func>tool_name</func>
  <param1>value1</param1>
  <param2>value2</param2>
</tool_call>
```

**Basic Example** (no parameters):
```xml
<tool_call>
  <func>system.get_time</func>
</tool_call>
```

**Basic Example** (with parameters):
```xml
<tool_call>
  <func>im.send</func>
  <to>"ai-dev@ai"</to>
  <content>"message content"</content>
</tool_call>
```

**Multi-line Text Parameter**:
```xml
<tool_call>
  <func>filesystem.write</func>
  <path>"/path/to/file.txt"</path>
  <content>"line 1
line 2
line 3"</content>
</tool_call>
```

**Parameter Value Rules**:

| Type | Format | Example |
|------|--------|---------|
| **String** | Wrap with double quotes | `<query>"weather forecast"</query>` |
| **Number** | Write directly | `<count>10</count>` or `<price>3.14</price>` |
| **Boolean** | Write True/False | `<enabled>True</enabled>` |
| **List** | Use square brackets | `<items>[1, 2, 3]</items>` or `<tags>["news", "tech"]</tags>` |

**Parameters with Special Characters** (use CDATA):
```xml
<tool_call>
  <func>im.send</func>
  <to>"ai-dev@ai"</to>
  <content><![CDATA[Content with <html> tags or special chars]]></content>
</tool_call>
```

**Complete Example**:
```xml
<tool_call>
  <func>websearch.search</func>
  <query>"weather forecast"</query>
  <max_results>10</max_results>
  <filters>["news", "blog"]</filters>
</tool_call>
```

**Task + Tool Demo (correct)**:
```xml
<task_start>测试 JSON 数据库 API</task_start>

<tool_call>
  <func>system.run_session_job</func>
  <cmd>"curl -s http://127.0.0.1:8080/health"</cmd>
</tool_call>

<tool_call>
  <func>system.run_session_job</func>
  <cmd>"curl -s -X POST http://127.0.0.1:8080/collections/users"</cmd>
</tool_call>
```

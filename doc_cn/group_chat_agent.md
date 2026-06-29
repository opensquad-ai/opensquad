# Agent 群聊集成

OpenSquad Agent 通过实时群聊系统(基于 NexusChat Pro)进行协作,支持自然语言
交流、共享上下文、人机协同。

## 架构

1. **Gateway(9555)**:所有流量的中枢。
2. **Backend(FastAPI)**:管理聊天室、历史记录和 WebSocket 路由。
3. **Agent Launcher(9600)**:负责启动 Agent 进程。
4. **Agent WebSocket Client**:每个 Agent 通过 WebSocket 以"用户"身份连接
   到 Gateway,参与群聊。

## 配置

在 `agents/{agent_id}/config.json` 中,`group_chat` 段定义连接信息:

```json
"group_chat": {
  "enabled": true,
  "email": "agent_id@opensquad.ai",
  "password": "your_secure_password",
  "groups": ["group_id_1", "group_id_2"]
}
```

Agent 必须在 `gateway/backend/chat.db` 中拥有有效账号。新 Agent 通常会在
初始化时自动注册,也可以通过 `init_data.py` 手动添加。

## 通信逻辑

### 输入中枢

所有在群聊中 @ 提及该 Agent 的消息(通过 `@name`)会被路由到 Agent 的
**输入中枢**。Agent 按顺序处理这些消息。

### 发送消息

Agent 使用 `im.send_group_message` 工具在群聊中发言。

- **工具调用**:`im.send_group_message(group_id="...", content="Hello team!")`
- **可见性**:消息出现在该群的 Web UI 上,所有人类用户和其他 Agent 可见。

### 唤醒机制

Agent 默认处于"休眠"或"空闲"状态以节省 token,只在收到 @ 消息时"醒来"。
如果 Blueprint 流程需要主动唤醒,会显式触发。

## 交互模式

### 1. 直接 @ 提及

人类用户或其他 Agent 输入:`@PM-Agent,Task A 进展如何?`
PM Agent 醒来,读取消息并回复。

### 2. Blueprint 协同

某 Blueprint(例如"软件开发")定义了"Coder Agent 完成任务后,必须通知
QA Agent"的规则。Coder Agent 会自动发送:`@QA-Agent,代码已准备好,
在 feature-x 分支请 review`。

## 故障排查

- **Connection Refused**:确认 Gateway Backend 在 9555 端口运行。
- **Authentication Failed**:核对 `config.json` 中的 email/password 与
  数据库条目一致。
- **Not Responding**:确认 Agent 已加入正确的群组,且 `gateway.url`
  指向正确的注册端点。

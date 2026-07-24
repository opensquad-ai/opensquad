# Cursor API 调用智能体 Skill

封装了通过 Cursor REST API 调用 AI 智能体的完整指南，包含持续对话和流式输出的代码示例。

## 包含内容

- `SKILL.md` — 主 Skill 文档：API 端点、模型 ID、Python/curl 调用示例、流式 SSE 处理、多轮对话上下文保持
- `README.md` — 本文件

## 安装

```bash
# 在项目目录下创建 cursor-api 目录，放入 SKILL.md 即可
# 或通过 OpenSquad 发布
```

## 使用

在 OpenSquad 中加载：

```python
agent_setup.read_skill("cursor-api")
```

然后按文档中的代码示例调用 Cursor API。

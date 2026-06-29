# OpenSquad 协同开发守则 (Git/VCS)

本守则定义了多代理（Multi-Agent）在共享工作区或局域网分布式环境中进行协同开发的标准流程与行为规范。

---

## 0. 局域网协同概述

OpenSquad 支持多个 Agent 部署在局域网不同机器上，通过共同连接同一台自建 Git 服务器（如 Gitea）实现分布式协同开发。各 Agent 各自拉取仓库、独立开发分支、推送后由审查 Agent 合并，讨论和决策在群聊中完成，无需 GitHub/PR 流程。

```
[局域网 Git 服务器 Gitea :3000]
     ├── Agent coder-001  (分支 coder-001/feat-login)
     ├── Agent coder-002  (分支 coder-002/feat-payment)
     ├── Agent pm         (管理 main 分支，负责合并)
     └── Agent qa         (拉取分支做测试)
```

代码协作通过 `git_core` 插件完成，讨论/评审通过群聊完成。

---

## 1. 配置局域网 Git 服务器

### 1.1 通过管理面板可视化配置（推荐）

1. 打开管理面板 → **Plugins** → 找到 **Git Core**
2. 点击右侧齿轮图标展开配置面板
3. 填写以下字段后点击 **Save**：

| 字段 | 说明 | 示例 |
|---|---|---|
| `git_server` | 局域网 Git 服务器地址 | `http://192.168.1.100:3000` |
| `default_remote` | 默认 remote 名称 | `origin` |
| `default_branch` | 默认分支名称 | `main` |
| `username` | HTTPS 认证用户名（留空则用 `oauth2`） | `teambot` |
| `access_token` | 访问令牌或密码（带遮罩） | `gp_xxxx...` |

配置保存后立即生效，无需重启 Agent。

> **access_token 获取方式（只需操作一次）**
>
> 以 Gitea 为例：
> 1. 浏览器打开 Gitea Web 界面（如 `http://192.168.1.100:3000`）
> 2. 右上角头像 → **Settings** → 左侧 **Applications**
> 3. 在 "Generate New Token" 处填写名称（如 `opensquad`），点击生成
> 4. 复制生成的 Token，粘贴到插件配置的 `access_token` 字段，保存
>
> Token 保存后所有 Agent 的 clone / push / pull 均自动携带认证，无需重复操作。
>
> **如果局域网 git 服务器不设密码**（纯内网可信环境），将 Gitea 仓库设为公开或关闭强制认证，`access_token` 留空即可，跳过上述步骤。

### 1.2 通过 system_config.json 配置（全局默认值）

适合为整个系统设置统一默认值，插件 UI 中保存的值优先级更高。

```json
"vcs": {
    "git_server": "http://192.168.1.100:3000",
    "default_remote": "origin",
    "default_branch": "main"
}
```

### 1.3 通过环境变量覆盖

```bash
export VCS_GIT_SERVER=http://192.168.1.100:3000
export VCS_DEFAULT_REMOTE=origin
export VCS_DEFAULT_BRANCH=main
```

**配置优先级**（由高到低）：
```
插件 UI 保存值 > 环境变量 > system_config.json > 内置默认值
```

---

## 2. 核心协作模型：分支隔离 + 群聊讨论

- **各自独立**：每个 Agent 在局域网 Git 服务器上操作自己的功能分支，互不干扰。
- **分支驱动开发**：严禁多个 Agent 同时在同一个分支上进行未提交的修改。
- **身份感知**：每次 `git.commit` 自动将 `GIT_AUTHOR_NAME` 设为该 Agent 的 `agent_id`，提交历史中可追溯贡献者。
- **讨论替代 PR**：代码评审、合并决策、冲突协商均在群聊中完成，无需 GitHub PR。

---

## 3. 角色职责

- **PM（项目经理）**：初始化仓库、维护 `main`/`dev` 分支、分配任务、执行最终合并。
- **Developer（开发 Agent）**：从基准分支切出功能分支，开发并推送。
- **Reviewer（审查 Agent）**：拉取功能分支，测试验证后在群聊通知 PM 合并。
- **QA（测试 Agent）**：拉取指定分支运行测试，将结果报告到群聊。

---

## 4. 标准操作流（Workflow）

### 4.1 首次接入仓库

```python
# 配置了 git_server 后，只需写相对路径，无需完整 URL
git.clone("team/project.git", "/workspace/project")
```

若未配置 `git_server`，则需写完整 URL：
```python
git.clone("http://192.168.1.100:3000/team/project.git", "/workspace/project")
```

### 4.2 开始一个任务

```python
# 切出以自己 ID 为前缀的功能分支
git.checkout("/workspace/project", "coder-001/feat-login", create=True)
```

### 4.3 开发与提交

```python
# 修改文件后...
git.add("/workspace/project", ["."])
git.commit("/workspace/project", "feat: 实现登录逻辑")
# author_name 自动注入为 agent_id，无需手动指定
```

### 4.4 推送分支

```python
# 配置了 default_remote / default_branch 后，参数可省略
git.push("/workspace/project")

# 首次推送建立跟踪关系
git.push("/workspace/project", set_upstream=True)

# 显式指定
git.push("/workspace/project", remote="origin", branch="coder-001/feat-login")
```

### 4.5 拉取其他人的更新

```python
# 拉取主分支最新变更
git.pull("/workspace/project", branch="main")

# 或只抓取不合并，手动决定
git.fetch("/workspace/project")
git.merge("/workspace/project", "origin/main")
```

### 4.6 交接评审（群聊）

开发 Agent 在群聊中通知：
> "@pm 我已完成 `coder-001/feat-login`，请拉取评审"

PM 或 Reviewer 拉取并检查：
```python
git.fetch("/workspace/project")
git.checkout("/workspace/project", "coder-001/feat-login")
git.log("/workspace/project")
```

### 4.7 合并与清理

评审通过后，由 PM 执行合并：
```python
git.checkout("/workspace/project", "main")
git.merge("/workspace/project", "coder-001/feat-login")
git.push("/workspace/project", branch="main")
git.branch("/workspace/project", "coder-001/feat-login", delete=True)
```

---

## 5. 冲突解决协议

合并或拉取时若出现冲突：

1. 调用 `git.status` 确认冲突文件（显示 `both modified`）
2. 读取冲突文件，解析 `<<<<<<< HEAD` / `=======` / `>>>>>>>` 标记
3. 根据上下文逻辑编辑并保留正确内容，删除所有冲突标记
4. 如有歧义，在群聊中 @ 相关 Agent 讨论后决定
5. 修复完成后：

```python
git.add("/workspace/project", ["path/to/resolved_file.py"])
git.commit("/workspace/project", "fix: 解决与 coder-002 的合并冲突")
```

---

## 6. 审计与透明度

- 所有 Git 操作必须通过 `git_core` 插件执行，操作日志自动写入 `data/audit/vcs_footprints.jsonl`。
- 严禁通过原生 `bash` 执行绕过 Hook 的 Git 命令（如 `git commit --no-verify`）。
- 可通过管理面板 **VCS Audit Timeline** 界面查看完整操作历史。

---

## 7. 工具速查表

| 工具 | 用途 | 关键参数 |
|---|---|---|
| `git.clone` | 克隆仓库（支持相对路径自动补全服务器地址） | `url`, `path` |
| `git.checkout` | 切换/创建分支 | `path`, `target`, `create=True` |
| `git.add` | 暂存文件 | `path`, `files=["."]` |
| `git.commit` | 提交（自动注入 agent_id 为作者） | `path`, `message` |
| `git.push` | 推送到远程（自动用配置的 remote/branch） | `path`, `set_upstream` |
| `git.pull` | 拉取并合并 | `path`, `remote`, `branch` |
| `git.fetch` | 仅抓取不合并 | `path`, `remote` |
| `git.merge` | 合并分支 | `path`, `source` |
| `git.rebase` | 变基 | `path`, `upstream` |
| `git.branch` | 列出/创建/删除分支 | `path`, `name`, `delete` |
| `git.status` | 查看工作区状态 | `path` |
| `git.diff` | 查看变更内容 | `path`, `cached` |
| `git.log` | 查看提交历史 | `path`, `limit` |
| `git.remote_add` | 添加 remote | `path`, `name`, `url` |
| `git.remote_set_url` | 修改 remote URL | `path`, `name`, `url` |

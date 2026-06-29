# 贡献指南

感谢你对 OpenSquad 的关注！  
在提交 issue 或 PR 之前，请先阅读本指南。

English version: [CONTRIBUTING.md](CONTRIBUTING.md)

**分支与发布流程：** 见 [BRANCHING_ZH.md](BRANCHING_ZH.md)（English：[BRANCHING.md](BRANCHING.md)）和 [RELEASING.md](RELEASING.md)。

---

## 目录

- [行为准则](#行为准则)
- [快速上手](#快速上手)
- [贡献方式](#贡献方式)
  - [报告 Bug](#报告-bug)
  - [功能建议](#功能建议)
  - [提交代码](#提交代码)
- [子项目：改什么去哪个仓](#子项目改什么去哪个仓)
- [开发环境搭建](#开发环境搭建)
- [编码规范](#编码规范)
- [提交信息规范](#提交信息规范)
- [PR 流程](#pr-流程)

---

## 行为准则

本项目遵循 [贡献者公约行为准则](CODE_OF_CONDUCT.md)，参与即表示同意遵守其条款。

---

## 快速上手

1. Fork 仓库：`https://github.com/opensquad-ai/opensquad`
2. 克隆你的 fork 到本地。
3. 先读 [BRANCHING_ZH.md](BRANCHING_ZH.md)——本项目使用 `main` + `dev` + `release/*` + `hotfix/*` 的多分支模型，不是单分支的 GitHub Flow。
4. 选对基础分支：
   - **新功能、bug 修复、文档、chore、refactor** → 从 `dev` 切出
   - **紧急生产修复** → 从 `main` 切出，命名 `hotfix/*`
5. 用规范名称切分支：`git checkout -b feature/<模块>-<简述> dev`
6. 修改代码、补充测试、提交 commit（遵循下面的 Conventional Commits）。
7. 推送分支并发起 Pull Request，**目标分支是 `dev`**（hotfix 是 `main`）。

> 默认基础分支是 `dev`。`main` 只接受 `release/*`（打 tag 发版）
> 和 `hotfix/*`（紧急修复）的合并。完整策略、命名规范、示例、
> 发布/紧急修复流程见 [BRANCHING_ZH.md](BRANCHING_ZH.md)。

---

## 贡献方式

### 报告 Bug

- 提交前请先搜索现有 issue，避免重复。
- 使用 **Bug Report** issue 模板。
- 请提供：操作系统、Python 版本、复现步骤、预期行为与实际行为、相关日志。

### 功能建议

- 使用 **Feature Request** issue 模板。
- 说明你要解决的问题，以及为什么这个方案是最合适的。

### 提交代码

- 每个 PR 只关注一件事。
- PR 描述中链接相关 issue（例如 `Closes #42`）。
- 所有 CI 检查通过后方可合并。

---

## 开发环境搭建

推荐使用 [uv](https://github.com/astral-sh/uv)：

```bash
uv sync
cd src/opensquad/gateway/nexuschat-pro && npm install && cd ../../../..
uv run pytest tests/
```

### 安装目录 vs 工作区

- **安装目录**：本 git 仓库（`src/opensquad`、`src/plugins` 等）。
- **工作区**：运行时数据（默认 `~/.opensquad/workspace`）—— Agent、`data/plugins` 配置、日志。**不要提交工作区内容。**

首次运行前复制示例配置：

```bash
cp system_config.example.json system_config.json
# 编辑 node_secret；勿将真实配置提交到 git
```

### Pre-commit

```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files
```

### 新手友好贡献方向

| 方向 | 入口 |
|------|------|
| 文档 | `doc_cn/`、`docs/README.md`——见下文[文档目录结构](#文档目录结构) |
| 插件 | `src/plugins/` |
| Skill | `src/skills/` |
| 测试 | `tests/` |
| 前端 | `src/opensquad/gateway/nexuschat-pro/` |

可使用 **Good First Contribution** Issue 模板。

## 编码规范

| 方向 | 规范 |
|------|------|
| Python | PEP 8；鼓励使用类型注解 |
| TypeScript / React | ESLint + Prettier（配置在 `nexuschat-pro/`） |
| 导入顺序 | Python 使用 `isort`；优先使用绝对导入 |
| 测试 | 使用 `pytest`；新功能需在 `tests/` 下附带测试 |
| 文档字符串 | 公开函数/类使用 Google 风格 |

---

## 提交信息规范

遵循 [Conventional Commits](https://www.conventionalcommits.org/) 规范：

```
<类型>(<范围>): <简短描述>

[可选正文]

[可选尾注]
```

常用类型：`feat`、`fix`、`docs`、`refactor`、`test`、`chore`、`ci`。

示例：
```
feat(plugins): 新增 vcs_remote user_info 工具
fix(gateway): 优雅处理 node_secret 缺失的情况
docs: 更新 CONTRIBUTING 开发环境章节
```

---

## PR 流程

1. 确保所有测试通过（`pytest tests/`）。
2. 若行为发生变化，请更新相关文档。
3. 维护者将在数个工作日内完成评审。
4. 默认采用 Squash and Merge 合并策略。

### 关键安全规则

- 日常流程不要使用 `git add -A`
- 只暂存明确文件
- 本地配置与运行数据不得被跟踪（CI guard 会阻断）

---

## 子项目：改什么去哪个仓

OpenSquad 拆成了**多个仓库**。把 PR 开在对的仓里——往本仓提新插件、
或往 skills 仓提 runtime 改动，会被直接关闭并打回。

| 你在改什么                                                           | 仓库                                                                                       | 分支模型                                |
|----------------------------------------------------------------------|--------------------------------------------------------------------------------------------|-----------------------------------------|
| **核心框架**（gateway、agent runtime、runner、tools、CLI、launcher） | **[opensquad-ai/opensquad](https://github.com/opensquad-ai/opensquad)**（*本仓*）          | `main` + `dev` + `release/*` + `hotfix/*` |
| **角色卡**（agent 角色定义 / persona / 人物设定）                     | [opensquad-ai/opensquad-roles](https://github.com/opensquad-ai/opensquad-roles)            | 简化版（PR → `main`，见下）             |
| **协作卡**（群聊模板、多 agent 场景剧本）                             | [opensquad-ai/opensquad-collab-cards](https://github.com/opensquad-ai/opensquad-collab-cards) | 简化版（PR → `main`）                   |
| **技能**（agent 能力、工作流配方）                                    | [opensquad-ai/opensquad-skills](https://github.com/opensquad-ai/opensquad-skills)          | 简化版（PR → `main`）                   |
| **插件**（渠道适配器、工具集成、非核心插件）                          | [opensquad-ai/opensquad-plugins](https://github.com/opensquad-ai/opensquad-plugins)        | 简化版（PR → `main`）                   |

> 桌面客户端的"插件市场"界面已经把贡献者指向 `opensquad-ai/opensquad-plugins`
> 来提交新插件。

### 内置 vs 贡献内容（本仓）

本仓（`opensquad-ai/opensquad`）只发**内置核心插件**——每个 OpenSquad
安装都必需的系统级工具。它们列在 [`src/plugins/builtin_plugins.json`](src/plugins/builtin_plugins.json)
里，默认启用，不可卸载。

其它所有内容（角色卡、协作卡、技能、非核心插件）都在上面表里对应
的子项目仓里。要贡献，**在那边**开 PR，不要开在本仓。

### 子项目的分支模型

子项目（roles / collab-cards / skills / plugins）发的是**声明式内容**
（Markdown / JSON / 无 runtime 耦合的 Python），所以用更简化的流程：

- 单 `main` 分支，受保护。
- 功能分支：`feature/<name>`、`fix/<name>`、`docs/<name>`。
- PR 直接 target `main`；squash merge；分支自动删除。
- `main` 只接受 CI 绿 + 已 review 的合并。
- 不需要 `dev` / `release/*` 的开销——内容小，review 几分钟，
  任何 regression 用一个 follow-up PR revert 即可。

完整的 `main` + `dev` + `release/*` + `hotfix/*` 模型留给**本核心框架仓**，
因为一个坏 merge 会让所有人的 runtime 挂掉。详见 [BRANCHING_ZH.md](BRANCHING_ZH.md)。

### 跨仓改动

如果改动同时涉及核心框架和子项目：

1. 先开子项目 PR（合并便宜，feedback 快）。
2. 再开核心框架 PR，PR 正文里引用子项目 PR 号（例：`Depends on opensquad-ai/opensquad-skills#42`）。
3. 维护者会排序合并；**不要**在子项目那边进之前强合核心 PR。

## 文档目录结构

本仓有三个文档目录，各有各的用途。把文档放错目录（或者用错命名约定）
是让未来读者最困惑的事之一。

| 目录 | 用途 | 命名 |
|------|------|------|
| `doc_en/` | 英文用户指南（getting started、架构、部署、插件开发、排障…）| `FOO.md`——不需要后缀 |
| `doc_cn/` | 中文用户指南——`doc_en/` 的镜像 | `FOO.md`——不需要后缀；目录本身就隐含语言 |
| `docs/` | 跨语言、补充、**维护者向**文档（安全基线、GitHub 设置、本仓自身的元 README）| `FOO.md`——内容应当语言中立或仅服务维护者；不放 user-facing 的 EN/ZH 内容 |
| 根目录 | 项目级文件（`README.md`、`BRANCHING.md`、`CHANGELOG.md`、`CONTRIBUTING.md`、`SECURITY.md`、`LICENSE`）| `FOO.md`（EN）/ `FOO_ZH.md`（CN）——后缀消歧，因为同目录 |

### 规则

- **双语 user-facing 文档** → `doc_en/` 和 `doc_cn/` 用同样的 `FOO.md` 文件名。如果只
  有一门语言，先放到对应语言目录里；另一门是 follow-up——**不要**把单语言的
  user 文档留在 `docs/`。
- **仅维护者向文档**（安全基线、仓库设置、internal 开发报告）→ `docs/`。
- **项目级文档**（适用于整个项目，不只一个子系统）→ 根目录，中文用 `_ZH.md` 后缀。
- **`doc_en/` / `doc_cn/` 内部用 `_EN.md` / `_ZH.md` 后缀** → 后缀是冗余的，
  改名为 `FOO.md`。目录已经隐含语言。

### 不要

- ❌ 把 user-facing 英文内容放 `docs/`——用 `doc_en/`
- ❌ 把 user-facing 中文内容放 `docs/`——用 `doc_cn/`
- ❌ 在 `doc_en/` / `doc_cn/` 内部用 `_EN.md` / `_ZH.md` 后缀——目录已经隐含语言
- ❌ 在 `docs/` 里用 `_ZH.md` 后缀（`docs/` 不是中文目录；如果文档只对中文
  维护者相关，不加后缀直接放 `docs/`、内容写中文就行）
- ❌ 同样地不要在 `docs/` 里用 `_EN.md` 后缀

### 例子

| 要加的文档 | 放哪 | 文件名 |
|------------|------|--------|
| Kubernetes 部署的英文指南 | `doc_en/` | `kubernetes_deployment.md` |
| 同一份指南的中文镜像 | `doc_cn/` | `kubernetes_deployment.md` |
| Q2 性能测量的内部报告 | `docs/` | `perf-q2-2026.md` |
| `agent_factory` 插件的中文详细指南 | `doc_cn/` | `agent_factory_guide.md`（不加 `_ZH` 后缀）|
| Agent 管理参考文档（中英双语镜像对） | `doc_en/` + `doc_cn/` | `agent_management.md`（同名镜像对）|
| 项目级中文 release notes 草稿 | 根目录 | `RELEASE_NOTES_ZH.md` |

各目录的当前内容看对应 README：

- [doc_en/README.md](doc_en/README.md) — 英文用户指南索引
- [doc_cn/README.md](doc_cn/README.md) — 中文用户指南索引
- [docs/README.md](docs/README.md) — `docs/` 里的内容及原因

---

*OpenSquad Contributors — 基于 MIT 许可证发布*

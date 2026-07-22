# 分支策略——如何负责一个模块

这份指南只回答一个问题：

> "我负责 `<X>` 模块的改动，应该从哪条分支切？怎么切回去？"

它是对 [CONTRIBUTING_ZH.md](CONTRIBUTING_ZH.md) 和面向维护者的
[RELEASING.md](RELEASING.md) 的补充。准备动手写代码前先看一遍。

English version: [BRANCHING.md](BRANCHING.md)

> **适用范围**：本模型只适用于**本仓**（`opensquad-ai/opensquad`，
> 核心框架）。四个子项目——[opensquad-ai/opensquad-roles](https://github.com/opensquad-ai/opensquad-roles)、
> [opensquad-ai/opensquad-collab-cards](https://github.com/opensquad-ai/opensquad-collab-cards)、
> [opensquad-ai/opensquad-skills](https://github.com/opensquad-ai/opensquad-skills)、
> [opensquad-ai/opensquad-plugins](https://github.com/opensquad-ai/opensquad-plugins)——
> 发的是声明式内容，用的是**更简化**的流程（PR → `main`）。拆分原因
> 见 [CONTRIBUTING_ZH.md → 子项目](CONTRIBUTING_ZH.md#子项目改什么去哪个仓)。

---

## 1. 全局分支图

```
                           ┌──────────────┐
                           │   main       │  ◄── 稳定分支，只有 vX.Y.Z tag
                           └──────▲───────┘
                                  │ (来自 release/* 的 merge commit，
                                  │  来自 hotfix/*，或从 dev 的反向 merge)
                                  │
              ┌───────────────────┴───────────────────┐
              │                                       │
        ┌─────┴─────┐                          ┌──────┴──────┐
        │ release/  │ ◄── 从 dev 切            │  hotfix/*   │
        │ x.y.z     │  （patch 版本从 vX.Y.Z  │  (紧急，    │
        │           │   tag 切）              │   从 main)  │
        └─────▲─────┘                          └──────▲──────┘
              │ (PR 到 main，然后删分支)              │ (PR 到 main
              │                                       │  + cherry-pick
              │                                       │  回 dev)
              │     ┌──────────────────────────────┐  │
              └─────┤             dev              ├──┘
                    │ (长期集成分支)               │
                    └────────────▲─────────────────┘
                                 │ (squash merge 自)
                                 │
              ┌──────────────────┴──────────────────┐
              │                                     │
        ┌─────┴─────┐   ┌─────────┐   ┌─────┐   ┌────┴────┐
        │ feature/  │   │  fix/   │   │docs/ │   │ chore/ │
        │ <模块>    │   │<模块>   │   │      │   │        │
        └───────────┘   └─────────┘   └──────┘   └────────┘
        (你的工作分支，全部从 dev 切出)
```

### 发布线 vs 集成分支

- `release/x.y.z` 是**短命的工作分支**。它只为一次发版周期存在：
从 `dev` 切（下一个 minor 或 major 版本），或者从已有的 `vX.Y.Z` tag
切（patch 版本）；用来改版本号、更新 CHANGELOG、做 QA；然后 PR
到 `main`，**合并后立刻删除**。**Tag，不是分支，才是已发布版本的
长寿命记录。**
- `hotfix/*` 的形态完全一样，只是用在"等不到下一次计划发版，必须立刻修"
的紧急生产修复上：从 `main` 切，最小修复，PR 到 `main` 并打 tag，
然后删除。
- `dev` 是**集成分支**：所有 `feature/*`、`fix/*`、`docs/*`、`chore/*`
PR 都先 squash merge 到这里。
- `main` 接收三类合并：来自 `release/x.y.z`（计划发版的毕业）、来自
`hotfix/*`（紧急修复），以及来自 `dev` 的反向 merge（定期"吸收
dev"，保证 main 跟 dev 不会越走越远）。

> **不要把 `release/*` 或 `hotfix/*` 分支留着"以防万一"。**
> `vX.Y.Z` tag 才是事实来源。将来如果需要 patch，从那个 tag 重新切一条
> `release/x.y.(z+1)` 就行。

---

## 2. 选对基础分支

问自己三个问题：


| 问题                         | 基础分支                                           |
| -------------------------- | ---------------------------------------------- |
| 我在加新功能 / 新模块吗？             | `dev`                                          |
| 我在修一个非紧急的 bug 吗？           | `dev`                                          |
| 我在改文档（不是错别字 / 失效链接）？       | `dev`                                          |
| 我在为 `x.y.z` 切一条新的发布线吗？     | `dev`（下一个 minor/major）或已有的 `vX.Y.Z` tag（patch） |
| 我在修一个**现在就要上**的紧急生产 bug 吗？ | `main` → `hotfix/*`                            |


> 默认就是 `dev`。`release/x.y.z` 和 `hotfix/*` 都是维护者工作流；
> 不要在它们上面切常规 feature。

---

## 3. 分支命名规范

所有工作分支都遵循：

```
<类型>/<模块>-<简述（kebab-case）>
```


| 类型          | 使用场景                | 示例                                   |
| ----------- | ------------------- | ------------------------------------ |
| `feature/`  | 新功能、新模块、新 API       | `feature/plugin-store-rating-filter` |
| `fix/`      | 非紧急 bug 修复          | `fix/gateway-node-secret-missing`    |
| `release/`  | 短命的发版准备分支（仅维护者切）    | `release/0.3.0`                      |
| `hotfix/`   | 紧急生产修复（从 `main` 切）  | `hotfix/rotate-leaked-secret`        |
| `docs/`     | 纯文档改动（错别字 / 失效链接可省） | `docs/branching-typo`                |
| `chore/`    | 重构、CI、工具，无行为变更      | `chore/ruff-pin-0.6`                 |
| `refactor/` | 同行为的代码结构重组          | `refactor/agent-message-bus`         |
| `test/`     | 只加测试，无生产代码改动        | `test/plugin-store-coverage`         |


> `release/x.y.z` 和 `hotfix/*` 都是短命的，毕业到 `main` 后会被删除；
> 留下来的是 `vX.Y.Z` tag。

### `<模块>` 一览

`<模块>` 应当对应仓库里的一个顶层区域，方便分流时一眼看清：


| 仓库路径                                   | `<模块>` 取值    |
| -------------------------------------- | ------------ |
| `src/opensquad/gateway/`               | `gateway`    |
| `src/opensquad/gateway/nexuschat-pro/` | `gateway-ui` |
| `src/plugins/`                         | `plugins`    |
| `src/skills/`                          | `skills`     |
| `src/opensquad/agent/`                 | `agent`      |
| `src/opensquad/tools/`                 | `tools`      |
| `doc_en/` / `doc_cn/` / `docs/`        | `docs`       |
| `tests/`                               | `tests`      |
| `.github/workflows/`, `scripts/`       | `ci`         |


如果改动跨多个模块，建议拆成 stacked PR；一定要合在一个分支里的话，
分支名取"主要模块"，在 PR 正文里说明涉及的其他模块。

---

## 4. 完整示例

### 示例 A —— "我要做 user-auth 模块"

```bash
# 1. 同步 dev
git fetch upstream
git checkout dev
git rebase upstream/dev
git push origin dev

# 2. 切工作分支
git checkout -b feature/agent-user-auth

# 3. 写代码
git add src/opensquad/agent/auth.py tests/test_agent_auth.py
git commit -m "feat(agent): 新增用户认证模块

- 基于 JWT 的会话 token
- login / logout / refresh 端点
- 新代码 100% 覆盖"

# 4. push 并开 PR
git push -u origin feature/agent-user-auth
# 开 PR: feature/agent-user-auth -> dev
```

### 示例 B —— "我发现 plugin loader 有个 bug"

```bash
git fetch upstream
git checkout dev
git rebase upstream/dev
git checkout -b fix/plugin-loader-import-error

# ... 修 bug ...
git commit -m "fix(plugins): 处理 plugin 发现流程中缺失的 __init__

当插件目录缺少 __init__.py 时 loader 会抛 ImportError。
现在改为跳过并打一条清晰的日志。

Closes #312"

git push -u origin fix/plugin-loader-import-error
# 开 PR: fix/plugin-loader-import-error -> dev
```

### 示例 C —— "生产环境出安全问题了，要立刻修"

```bash
# 从 main 切，不要从 dev —— 我们要最小、最快的修复。
git fetch upstream
git checkout main
git rebase upstream/main
git checkout -b hotfix/disable-leaky-log

# ... 最小修复 + 测试 ...
git commit -m "fix(gateway): 在日志中遮蔽 Authorization 头

0.4.2 的一个回归让 Authorization 头在 DEBUG 日志中被完整输出。
本次提交全局遮蔽该头。

CVE: 待分配
Closes #987"

git push -u origin hotfix/disable-leaky-log
# 开 PR: hotfix/disable-leaky-log -> main（不是 dev）

# 合并后：在 merge commit 上打 v0.4.3，把修复 cherry-pick 回 dev，
# 然后删除 hotfix 分支。完整步骤见第 6 节的"发版后清单"。
```

### 示例 D —— "我想改一下 API 文档"

```bash
git fetch upstream
git checkout dev
git rebase upstream/dev
git checkout -b docs/agent-api-reference

# ... 改 doc_en/、doc_cn/ ...
git commit -m "docs(agent): 澄清 on_message 钩子签名

补充 v2 钩子的示例，并加一条 v1 的废弃说明。"

git push -u origin docs/agent-api-reference
# 开 PR -> dev
```

错别字 / 失效链接可以走 `main`，但任何"有内容"的文档改动请走 `dev`，
随下一个版本一起发布。

### 示例 E —— "我要发新版 `v0.3.0`"（仅维护者）

```bash
# 1. 同步 dev
git fetch upstream
git checkout dev
git rebase upstream/dev
git push origin dev

# 2. 从 dev 切短命的 release 分支
git checkout -b release/0.3.0

# 3. 改版本号 + CHANGELOG（完整 checklist 见 RELEASING.md）
#    - pyproject.toml: version = "0.3.0"
#    - CHANGELOG.md:   把 [Unreleased] 项搬到 "## [0.3.0] - YYYY-MM-DD"
git add pyproject.toml CHANGELOG.md
git commit -m "chore(release): bump version to 0.3.0"

# 4. QA：在这个分支上安装、跑 smoke test、修复任何问题。
#    每个修复都是 release 分支上的普通 commit。

# 5. push 并开 PR 到 main（merge commit，不是 squash）
git push -u origin release/0.3.0
gh pr create --base main --head release/0.3.0 \
    --title "chore(release): 0.3.0" --body "见 RELEASING.md"
gh pr merge <PR#> --merge --delete-branch
# （--delete-branch 会删远端，本地分支留到第 9 步再清。）

# 6. 在 main 的 merge commit 上打 tag
git checkout main && git pull origin main
git tag -a v0.3.0 -m "v0.3.0"
git push origin v0.3.0

# 7. 把发版吸收进 dev，避免 dev 漂移
git checkout dev
git merge --no-ff main -m "Merge branch 'main' into dev (absorb v0.3.0)"
git push origin dev

# 8. 把 dev 升到下一个 dev 版本
#    pyproject.toml: version = "0.4.0.dev0"
git add pyproject.toml
git commit -m "chore(dev): bump to 0.4.0.dev0 after v0.3.0 release"
git push origin dev

# 9. 清掉本地的 release 分支 —— v0.3.0 tag 才是记录
git branch -D release/0.3.0
```

#### 什么时候 minor、什么时候 patch（还有 `.devN` / `.postN` 标记）

项目遵循 [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html)
（`MAJOR.MINOR.PATCH`），第 4 条有个关键警告：

> Major version zero (0.y.z) is for initial development. Anything may
> change at any time. The public API should not be considered stable.

也就是说 `0.x.y` 期间：

- **MINOR 升级**是真正的有意义的版本单位，**可以包含 breaking change**
- **PATCH 升级**只放同一 minor 线内的纯 bug 修复
- `1.0.0` 是 API 锁定的里程碑


| 升级                            | 什么时候                                                        |
| ----------------------------- | ----------------------------------------------------------- |
| `0.x.y` → `0.x.(y+1)`（PATCH）  | 一起发的一批纯 bug 修复。没有新的对外接口、没有新的必填配置、没有首启 UX 变化。老部署升级时啥也不用做。    |
| `0.x.y` → `0.(x+1).0`（MINOR）  | 新的用户操作流（比如首启向导）、新的对外 API / 端点、新的必填配置、新的鉴权机制，或 dev 上累积的一批功能。 |
| `0.x.y` → `0.x.y.postN`（POST） | 对**已发版**版本的紧急修复，但又够不上新 minor/patch 的门槛。保留当前 minor 线的最低破坏。   |
| `0.x.y` → `1.0.0`（MAJOR）      | 公共 API 正式稳定。之后任何 breaking change 都要走 major 升级。              |


PEP 440 的 dev / post 标记跟上面配合用在开发线上：


| 标记                       | 含义                               | 出现位置         |
| ------------------------ | -------------------------------- | ------------ |
| `0.X.0.dev0`             | "开始开发 0.X.0"                     | `dev` 分支头    |
| `0.X.0.dev5`             | "迭代中，这是第 5 个开发快照"                | 长期 dev 周期    |
| `0.X.0a1` / `b1` / `rc1` | alpha / beta / release candidate | 预发布 tag      |
| `0.X.0`                  | "0.X.0 已发版"                      | `vX.Y.Z` tag |
| `0.X.0.postN`            | "发版后的第 N 个修复，不算新发版"              | hotfix tag   |


**维护者速判法**：问自己"升级时**部署者要做什么**？"

- 答"啥也不用做，自动就好了" → PATCH
- 答"要做一次性操作（走向导、生成 secret、配新端点）" → MINOR
- 答"老数据 / 老流程会断，要迁移" → MAJOR 候选（0.x 阶段也可以走 MINOR）

### 示例 F —— "我要给已经发版的 v0.2.0 打 patch"

v0.2.0 已经发版了，但里面有个回归 bug，等不到 v0.3.0。从已有的 tag
切一条 patch release 分支 —— 流程跟普通发版一样，只是 base 是老 tag
而不是 dev。

```bash
# 1. 从已有的 v0.2.0 tag 切，不要从 dev 或 main
git fetch upstream --tags
git checkout v0.2.0
git checkout -b release/0.2.1

# 2. 应用最小修复（cherry-pick 或直接改）
# ... 补一个回归测试 ...
git add src/.../buggy.py tests/...
git commit -m "fix(gateway): 修复 v0.2.0 中的 X 回归

v0.2.x 线的 backport，修复 #NNN。

Fixes #NNN"

# 3. 升版本号
#    pyproject.toml: version = "0.2.1"
git add pyproject.toml
git commit -m "chore(release): bump to 0.2.1"

# 4. PR 到 main、打 tag、吸收进 dev、升 dev 版本 —— 跟示例 E 同形
git push -u origin release/0.2.1
gh pr create --base main --head release/0.2.1 \
    --title "chore(release): 0.2.1" --body "#NNN 的 backport"
gh pr merge <PR#> --merge --delete-branch

git checkout main && git pull
git tag -a v0.2.1 -m "v0.2.1" && git push origin v0.2.1

# cherry-pick 那个修复 commit（**不是**升版本号的 commit）到 dev
git checkout dev
git cherry-pick <fix-commit-sha>
git push origin dev

# 9. 清掉本地的 release 分支
git branch -D release/0.2.1
```

---

## 5. 让分支保持健康

PR 还没合并期间：

- **每天同步**。push 新 commit 前先 rebase：
  ```bash
  git fetch upstream
  git rebase upstream/dev     # hotfix 走 upstream/main
  git push --force-with-lease origin <your-branch>
  ```
- **不要 `git merge**`。Rebase 保持线性；merge 出来的"Merge branch 'dev' into
feature/x"最终会被 squash 掉，但过程中会造成评审者困扰。
- **跑本地质量门禁**（见 [CONTRIBUTING_ZH.md → 编码规范](CONTRIBUTING_ZH.md#编码-规范)）。
- **保持单一关注点**。范围变大就开新分支 / 新 PR，不要把无关改动塞进同一个。
- **release 分支的 QA loop 一定要紧凑**。release 分支上的每个修复 commit
都会进入发版历史；本地 squash 干净再 push，永远不要把 `wip`、`再试一次`
之类的 commit 推上去。

---

## 6. "完成"的判定标准

你的分支满足以下**全部**条件后才能合并：

- [ ] 分支名遵循 `<类型>/<模块>-<简述>` 规范。
- [ ] 基础分支选对（默认 `dev`，hotfix 才走 `main`）。
- [ ] commit 遵循 [Conventional Commits](https://www.conventionalcommits.org/)。
- [ ] PR 描述链接了对应 issue（`Closes #N` 或 `Refs #N`）。
- [ ] PR 模板全部填齐（没删任何章节）。
- [ ] 全部 CI 检查通过（lint、pytest、前端 smoke、文档链接、
  ```
  密钥扫描、SAST、SCA）。
  ```
- [ ] 触动目录对应的 CODEOWNER 已审批。
- [ ] 没有 `system_config*.json`、没有 workspace 数据、没有密钥。
- [ ] 分支已 rebase 到目标分支（无 merge commit）。
- [ ] 至少 1 个 approve。

通过后维护者会 squash merge（`feature/*`、`fix/*`、`docs/*` 等），
关联分支会被自动删除。

### 发版负责人的"合并后清单"

如果这次合的是 `**release/x.y.z` 的毕业**或 `**hotfix/*` 到 `main**`，
合并完成 ≠ 事情做完。发版负责人还必须做：

- [ ] 在 `main` 的 merge commit 上打 `vX.Y.Z` tag 并 push。
  ```
  `git tag -a vX.Y.Z <merge-sha> -m "vX.Y.Z" && git push origin vX.Y.Z`
  ```
- [ ] 删除 `release/x.y.z` / `hotfix/*` 分支（本地 + 远端）。
  ```
  `vX.Y.Z` tag 才是长寿命的记录，分支只是草稿。
  ```
- [ ] 把 `main` 反向 merge 回 `dev`（release），或者把修复 commit
  ```
  cherry-pick 到 `dev`（hotfix），避免 dev 漂移。
  ```
- [ ] 把 dev 的 `pyproject.toml` 升到下一个 `*.dev0` 版本并 push。

任何一步漏掉，dev 都会悄悄跟 main 脱节，下一次发版会从一个过期的 base 开始。

---

## 7. 常见坑


| 坑                                             | 正确做法                                                                                                                  |
| --------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| 新功能从 `main` 切                                 | 改成从 `dev` 切，关掉旧 PR 开新的。                                                                                               |
| hotfix 从 `dev` 切                              | 改成从 `main` 切；不要把 `dev` 反向合进 hotfix。                                                                                   |
| 把 `release/*` 分支留着"等下个 patch"                 | 直接删。将来 patch 从 `vX.Y.Z` tag 重新切。                                                                                      |
| 给 merge commit 打 tag 后忘了删分支                   | `git push origin --delete release/x.y.z` 然后 `git branch -D release/x.y.z`。                                            |
| 忘了打 tag                                       | `git tag -a vX.Y.Z <merge-sha>` 再 `git push origin vX.Y.Z`。                                                           |
| 跳过 `main → dev` 的吸收步骤                         | release PR 合并后跑 `git checkout dev && git merge --no-ff main`。                                                         |
| 分支叫 `my-branch`、`test`、`asdf`                 | 改名：`git branch -m feature/<模块>-<简述>`。                                                                                 |
| commit 是 `wip`、`fix typo`、`asdf`              | 本地 squash 干净再 push。                                                                                                   |
| 直接 `git push --force` 不带 `--force-with-lease` | 改用 `--force-with-lease`，避免把同事的 commit 覆盖掉。                                                                            |
| 一个 PR 改了 30+ 文件、8 个模块                         | 按模块拆成 stacked PR。                                                                                                     |
| `git add -A` 一把梭                              | 显式 `git add` 文件；CI guard 会拦截泄露的工作区数据。                                                                                 |
| 拿不准 PATCH 还是 MINOR                            | 用 [示例 E § 什么时候 minor、什么时候 patch](BRANCHING_ZH.md#示例-e--我要发新版-v030仅维护者) 里的"部署者升级时要做什么"判断法：要做任何事 = MINOR，啥也不用做 = PATCH。 |


---

*有疑问？在
[OpenSquad discussions](https://github.com/opensquad-ai/opensquad/discussions)
开贴讨论，或者在 PR 里 `@maintainers` 提醒。*

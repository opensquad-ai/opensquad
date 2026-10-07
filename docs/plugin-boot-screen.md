# Plugin-contributed boot screen (缝 B)

> **状态：已实现并通过验证。** 定稿方案：**核心拥有开屏动画的位置，插件只声明"放什么"**，
> 不需要往 `index.html` 注入任何插件代码（那是缝 A，见 §6）。
>
> **私有与发行分离**：私有动画放在 **workspace 插件目录**（仓库之外，永不进 wheel / 桌面安装包），
> 公开发行则用 OpenSquad 自己带的默认 loader / 内置插件。见 §4。

---

## 1. 问题与目标

想要"以插件形式定制 OpenSquad 的开屏动画"，且**私有的自己用、发行的用官方的**。

现状（侦察结论）：

- 网页版的开屏位置是 `nexuschat-pro/index.html` 里的 boot loader（`#root` 内，React 挂载时被替换）；
- 桌面的启动占位是 `electron/main.ts` 里的 `splash.html`（后端就绪之前那一屏）；
- 插件的浏览器侧资源只能在"用户导航到某个 view 时"由前端加载（`plugin-views/registry.ts`）——
  **没有**"应用启动前"的挂载点。

所以缝 B 不去造那个钩子，而是：

> **开屏动画由核心渲染**（位置本来就在核心手里），核心在启动时向一个只读端点问一句
> "有没有插件声明了开屏动画、放什么"，然后由核心播放。插件**不在 shell 里执行任何代码**。

## 2. 契约

### 2.1 插件侧（`plugin.json`）

```json
{
  "name": "my_boot_screen",
  "enabled": true,
  "contributes": {
    "bootScreen": {
      "videos": ["assets/1.mp4", "assets/2.mp4", "assets/3.mp4"],
      "poster": "assets/boot.jpg",
      "holdMs": 6000
    }
  }
}
```

| 字段 | 必填 | 含义 |
|---|---|---|
| `videos` | 二选一 | **播放列表**，按声明顺序轮换；每条是相对**本插件目录**的路径（拒绝绝对路径、`..`、URL scheme），文件不存在的条目被丢弃 |
| `video` | 二选一 | 单片简写，等价于 `videos: [x]` |
| `poster` | 否 | 视频首帧封面，同样相对插件目录 |
| `holdMs` | 否 | 退出上限（毫秒）；后端把值夹到硬上限 `120000`，`0`/缺省 = 用默认 `120000` |

`videos` 与 `video` 至少给一个，且解析后至少要有一条真实存在的文件，否则视为没有声明。
声明**多条**才有轮换效果；只给一条时，表现就是那一条重播。

插件**不需要**写 Python、不需要 `tools`/`hooks`——它就是一个清单 + 素材。

### 2.2 后端（只读、免鉴权）

```
GET /api/ai-web/boot-screen                                   # 放什么
GET /api/ai-web/boot-screen/asset?kind=video|poster[&index=N] # 素材本身
```

- **免鉴权**：boot loader 在 React/登录之前运行，没有 token 可带。
- 配置端点只暴露"哪个已启用插件要放什么"，失败一律返回 `{"enabled": false}`（fail-open，
  退回默认四象限加载动画）。
- 素材端点**受限**：入参只有 `kind`（枚举）和 `index`（播放列表下标，只对 `kind=video` 有意义）。
  文件由解析器决定——**不能指向任意路径，也不暴露插件目录**；`index` 非整数或越界一律 404。
  用 `FileResponse` 服务，因此天然支持 `Range`（`<video>` 流式播放依赖它）。

响应：

```jsonc
{ "enabled": true, "source": "my_boot_screen", "plugin": "my_boot_screen",
  "videos": ["/api/ai-web/boot-screen/asset?kind=video&index=0&v=1791345040",
             "/api/ai-web/boot-screen/asset?kind=video&index=1&v=1791345041"],
  "poster": "/api/ai-web/boot-screen/asset?kind=poster&v=1791345031",
  "holdMs": 6000 }
// 或
{ "enabled": false }
```

`v=` 是素材 mtime（**换片子不需要清缓存**）；`index` 是它在播放列表里的位置。

### 2.3 前端（`index.html` boot loader）

一段内联脚本（无框架），把覆盖层插到 **`document.body`** 上（不是 `#root`——React 挂载会替换
`#root`，插在那里等于永远看不见）：

- **按播放列表轮换，直到应用就绪**：未就绪时 `ended` 就换下一段（`index = (index + 1) % len`，
  走完最后一段回到第一段）；**就绪后不打断当前这段**，让它放完再淡出进入——用户遇到的回归正是
  "界面一出现动画就被切掉"；
- **就绪信号**：React 换上真实界面（默认 boot loader `.boot-loader-wrap` 被替换，用
  `MutationObserver` 观测 `#root`）才算就绪，此后底部才显形"加载完成 · 点击或按任意键跳过动画"。
  在应用起来之前不给这个提示——那等于告诉用户"可以走了"，而界面还没准备好。页面本来就没有
  `.boot-loader-wrap` 时立即算就绪；
- **轮完一整圈都放不出来才放手**：单段 `error` 只是换下一段；连续 `playlist.length` 次失败
  （期间没有 `playing`）才淡出。注意轮换只由 `ended` 触发——**卡住但不发 `ended`** 的片段不会被跳过，
  只能等上限兜底；
- **上限 120s**（`holdMs` 给了用它，后端夹到 `120000`；缺省也是 `120000`）：只防"应用永远加载不出来，
  把用户永远挡在开屏页"这一种情况；
- **自动播放被拒 / 环境没有媒体栈** → 立即放手（一个放不出声画的覆盖层比默认 loader 更糟）；
- `prefers-reduced-motion: reduce` → 直接不注入；
- `fetch` 失败 / 无声明 → 静默不注入，走默认 loader。

**`z-index` 必须极高**（`2147483000`）：应用自己有不透明的全屏层（`App.tsx` 的
`fixed inset-0 z-50 bg-panel`、`DesktopUpdateOverlay` 的 `z-[9999]` 等），覆盖层低于它们时动画
还在放、只是被盖住——表现就是"动画没播完就进界面了"。`bootScreen.scan.test.ts` 把这个数值钉住。

## 3. 改动清单

| 文件 | 改动 |
|---|---|
| `src/opensquad/plugin_boot_screen.py` | 纯函数：按优先级扫插件目录 → 解析 `contributes.bootScreen`（`videos`/`video`，丢掉不存在的条目）→ 产出播放列表 URL + 按 `index` 定位素材 |
| `src/opensquad/gateway/backend/app/ai_web/routes/_main.py` | 两个免鉴权端点（配置 + 受限素材；素材端点带 `index`） |
| `src/opensquad/gateway/nexuschat-pro/index.html` | `.boot-screen` 样式 + 内联加载脚本 |
| `docs/plugin-boot-screen.md` | 本文 |
| `tests/test_plugin_boot_screen.py` | 解析器 / 优先级 / 受限 kind / 路由契约 / ASGI 端到端 |
| `.../utils/bootScreen.scan.test.ts` | 加载脚本的契约（reduced-motion、fail-open、挂 body、退出路径） |
| `.../utils/bootScreen.dom.test.ts` | 加载脚本**真跑**（jsdom） |

## 4. 私有 vs 发行（本特性的重点）

解析器按**优先级**搜索插件根：

| 顺序 | 目录 | 是否进发行包 |
|---|---|---|
| 1 | `<workspace>/plugins/<name>/`（`syscfg.workspace_plugins_dir()`） | **否** —— 工作区数据，仓库与安装包都不含 |
| 2 | `<builtin>/plugins/<name>/`（`syscfg.builtin_resources_dir("plugins")`，源码检出即 `src/plugins/`） | 是 |

于是：

- **本地私有**：把自己的片子放进 workspace 插件目录 → 覆盖官方的，仅本机可见、**永不进 wheel / 安装包**；
- **公开发行**：干净安装的 workspace 里没有这个插件 → 自动落到随包行为：内置插件（若声明了 `bootScreen`）
  或 `index.html` 里 OpenSquad 自己的四象限 loader / 桌面的静态 `splash.html`。

同一根内按目录名排序取第一个；`enabled: false` 或被禁用的插件直接跳过；声明了却**文件不存在**的
条目也跳过——绝不发一个会 404 的死 URL。

## 5. 安全

- **插件不在 shell 里执行代码**：核心只读 JSON 声明、由核心播放媒体。没有扩大信任边界
  （对照 mods 矩阵铁律 1/3：`Client` 元素被 refused 正是因为"让插件加载任意前端模块"）。
- **路径安全**：`videos`/`video`/`poster` 必须是插件内的相对路径（拒绝绝对路径、`..`、scheme），解析后要求文件存在。
- **素材端点受限**：只接受 `kind` 枚举和 `index` 下标（下标非整数/越界即 404）；没有"按路径取文件"的入口，也不整目录暴露
  （这一点比 `/api/plugins/static` 的整目录挂载更紧）。
- **同源**：URL 由后端拼出，不能指向外部域。

## 6. 为什么不走缝 A（往 `index.html` 注入插件脚本）

缝 A 需要在**三条**出 HTML 的路径（生产静态挂载 / Vite 反向代理 / Vite 掉线回退）都做注入，
还要新增"启用状态来源""就绪信号""任意脚本的信任模型与知情同意"——成本高一个量级，且等于开放了
任意代码执行面。缝 B 用"核心渲染、插件供料"拿到同样的产品效果，没有这些代价。

## 7. 明确不做

- 桌面 `splash.html` 保持静态（它在后端就绪**之前**显示，那时端点还不可达）；插件动画在 App URL 加载后由网页 boot loader 承担。
- 不做逐帧/交互式动画，不开放 `Client` 类任意模块。
- 不做"插件往 shell 注入任意 UI"（那才是缝 A，需先定信任分级）。

---

## 8. 怎么用（3 步，私有）

```bash
# 0. 找到你的 workspace 插件目录（默认在 ~/.opensquad/workspace/plugins）
PLUGINS=$(python -c "import sys;sys.path.insert(0,'src');from opensquad.system_config import syscfg;print(syscfg.workspace_plugins_dir())")

# 1. 建插件目录
mkdir -p "$PLUGINS/boot_mine/assets"

# 2. 放你的片子（mp4/webm；16:9 比较合适）。放多条就按顺序轮换，直到应用就绪
cp your-clip.mp4 "$PLUGINS/boot_mine/assets/1.mp4"
cp your-clip-2.mp4 "$PLUGINS/boot_mine/assets/2.mp4"

# 3. 声明它（只要一条也可以：写 "video": "assets/1.mp4"）
cat > "$PLUGINS/boot_mine/plugin.json" <<'JSON'
{
  "name": "boot_mine",
  "enabled": true,
  "contributes": { "bootScreen": { "videos": ["assets/1.mp4", "assets/2.mp4"], "holdMs": 6000 } }
}
JSON
```

然后**重启网关与 agent**（插件清单是启动时读的），刷新页面即可。

- **网页版**：刷新 `http://127.0.0.1:9555`（开发态 :5173 走同一条 `/api` 代理）。
- **桌面版**：重启 App。后端就绪前显示静态 `splash.html`，App URL 加载后由本特性接管。
- 换片子：直接替换 `assets/1.mp4` 等，刷新即可（URL 带 mtime，不需要清缓存）。
- 关掉：删 `contributes.bootScreen`，或把 `enabled` 设为 `false`。

## 9. 验证（已跑通）

| 面 | 证据 |
|---|---|
| 解析器（禁用 / 缺文件 / 路径穿越 / 上限 / 优先级 / `videos` 与 `video` / 播放列表丢死条目） | `pytest tests/test_plugin_boot_screen.py` — **33 passed** |
| **私有优先**：workspace 插件压过字母序在前的随包插件；干净安装落到随包；都没有则默认 loader | 同上（`test_the_operators_own_plugins_win_over_the_shipped_ones`） |
| 素材端点**受限**：`kind` 只认枚举，`../plugin.json`、`assets/boot.mp4` 这类值一律拒绝；`index` 非整数/越界不服务 | 同上（`test_resolve_asset_only_accepts_the_enum`、`test_an_out_of_range_index_is_not_served`） |
| 端点契约（eager router、两个都**免鉴权**、配置失败 fail-open、素材缺失 404） | 同上 |
| 真链路（配置 → URL → 素材）：**真实 3.6MB mp4 实跑** | 私有插件胜出；整片 `200` / 3675935 bytes / `content-type: video/mp4`；`Range` → **206** `bytes 0-1023/3675935`；`kind=poster`（无）与 `kind=/etc/passwd` 均 **404** |
| 挂载顺序（路由必须在静态 catch-all 之前） | 同上（源码守卫，对照 `main.py`） |
| 加载脚本**真跑**（jsdom）：注入到 body / 未就绪时 `ended` 换下一段 / 走完一圈回到第一段 / 一整圈都失败才放手 / 就绪后 `ended` 才退出 / 点击 / `holdMs` / 无声明 / 请求失败 / reduced-motion | `vitest utils/bootScreen.dom.test.ts` — **13 passed** |
| 加载脚本契约（含"轮换不是单条重放"、"失败满一圈才放手"、120s 上限） | `vitest utils/bootScreen.scan.test.ts` — **12 passed** |
| 回归（读 `index.html` 的既有测试 + 挂在同一 router 上的 mods 路由） | 前端整棵树 `vitest run` — **150 files / 1572 passed**；后端 `test_gateway_mods_routes.py` — **13 passed** |

未验证的一步：在**跑着的实例**上肉眼看画面——需要按 §8 放一段真片并重启。代码链路已实测到
"字节能从 URL 取回 200/206"，剩下的只是浏览器解码显示。

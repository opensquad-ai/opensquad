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

- **一访问就盖住内置四象限**：脚本一开始就把静态 `.boot-loader-wrap` 里那个四象限 glyph 用
  `visibility:hidden` 藏掉（**不删节点**：就绪判据还要靠它在不在——显示与否不影响
  `querySelectorAll`）。同时把上次的播放列表缓存在 `localStorage['opensquad.bootScreen']`，
  本次**先本地起播、不等网络**（实测 **9ms** 就开始放），联网结果回来后以它为准（列表相同就接着放，
  **不重设同一个 src**——那会重新加载并打断正在播的那段，也是 `play()` `AbortError` 的来源）；
- ⚠️ **样式的归属地是硬约束：`.boot-screen` 的 CSS 必须声明在 `<head>`，绝不能写在 `#root` 里面。**
  React 挂载时 `createRoot(#root).render()` 会清空 `#root` 的子节点——这段 CSS 原先就写在 `#root`
  内部，于是 **React 挂载那一刻（实测 ~600ms）样式整体消失**：覆盖层从"全屏 `fixed` +
  `z-index:2147483000`"退化成正常流里的普通 `div`，掉到页面底部看不见（实测 `position:static`、
  `top` = 视口高度 = 579），用户看到的是 **App 自己的四象限**。现象极具误导性：埋点里 `vis=0`
  （CSS 可见性判据）却肉眼看见四象限——因为 `vis` 不看遮挡、也看不出"样式表被删了"。
  `bootScreen.scan.test.ts` 用"规则必须出现在 `</head>` 之前、`#root` 必须在其后"把这条钉住；
- **卡顿 / 音画不同步**：主要来自 **App 自己首屏渲染阻塞主线程**（Long Task 实测动画窗口内 14 次
  长任务、最坏 348ms / 496ms 都在 1.8–2.3s 的 React 首屏），画面停住而声音继续。loader 一侧做了三件事：
  ① 就绪判定**节流到 ≥200ms 一次**（原先"每个宏任务"都要整棵 `#root` 查一遍 `svg[role=status]`，
  恰好砸在最忙的那几百毫秒上）；② **下一段的首帧预取推迟到当前段放到约四成**，不在开播瞬间并行拉
  一个 10MB 级文件；③ `.boot-screen video` 加 `will-change: transform` 提成独立合成层，主线程忙时
  画面仍由合成器推进；
- **退场必须"一定不再出声"**：`done()` 里 `playToken += 1` 作废在途的 `play()`/150ms 重试
  （否则它会在 `pause()` 之后把元素重新放起来——元素即便移出 DOM 也继续出声，用户报的
  "进了界面 BGM 还在响"），并 `removeAttribute('src') + load()` 卸源；`tryPlay` 里再加 `if (leaving) return`。
  实测点掉后页面里 `<video>` 数量为 0、全部 paused、`currentTime` 不再前进；
- **BGM**：素材自带音轨（实测三段都是 AAC 立体声 32kHz）。**未静音的自动播放会被浏览器拦**（页面加载
  没有用户手势），所以策略是"**先按有声音起播，被拦（`NotAllowedError`）再退回静音重试**"——出声是
  加分项，动画必须一定出来。埋点 `playing` 那条带 `muted=` 是实际结果，被拦时会多一条 `audio:blocked`。
  隐藏的抓帧 `preloader` 始终静音（`preloader.muted = true`），不会重复出声。浏览器是否放行取决于该
  站点的媒体参与度/用户交互历史：用户常访问时通常直接有声音；
- **首帧封面（poster）让首屏与换段都不空**：`<video>` 换 `src` 会丢帧、首帧又要等媒体解码
  （实测首段首帧 ~2.2s 才上屏——媒体请求排在启动期几百个模块请求后面），这两处原本露出的
  就是**底色**（用户看到的"白屏"）。做法：
  ① 每段一 `playing` 就用 canvas 抓它的首帧（缩到 480px 的 JPEG dataURL，上限 3 条）存进
  `localStorage['opensquad.bootScreen:posters']`，**下次访问在 0ms 就挂上**，首屏立刻有画面；
  ② 隐藏的 `preloader` 视频提前抓**下一段**首帧，`advance()` 里**先挂 poster 再换 src**
  （`advance` 埋点带 `cover=true` 即表示生效）——段与段之间没有底色空档。
  ③ 预取的 `preloader` **刻意保留 `src`**（不卸载）——只抓到一帧就卸源等于掐断下载，缓存里只剩
  一小段，换段那次 load 还得重新缓冲，画面就**停在下一段的首帧封面上"卡一会"**（用户报的正是这个）。
  留着它，浏览器会把整段吃完留在缓存里；实测换段时下一段基本已是 `readyState=4`，到新帧 57–76ms。
  首次访问（无缓存且插件没给 `poster`）仍有一瞬底色；插件在 `plugin.json` 里声明 `poster` 即可消除；
- **按播放列表轮换**：`ended` 就换下一段（`index = (index + 1) % len`，走完最后一段回到第一段）；
- **就绪 = 两件事同时成立**：① App 走出自己的启动 gate（`App.tsx` 在离开
  `isLoading || registrationStatus === 'unknown'` 时置 `<html data-opensquad-ready="1">`）；
  ② `#root` 里**不再有四象限 loader**（判据 `#root svg[role="status"]`：静态 logo 与每个
  `OpenSquadLoader` 都命中，而 `StatusBadge`/`PulseDotsStatus` 这类 `<span>`/`<div>` 不命中）。
  shell 用 `MutationObserver` 同时观测 `<html>` 属性和 `#root` 子树，而且是**双向**的——就绪之后
  App 若又回到全屏加载态，提示语收回、动画继续盖着。**任一单独成立都不够**："React 挂载"之后 App
  自己还要取配置/注册状态、再取会话/工作区，这几秒它一直显示同一个四象限 loader；
- **就绪后不自动进界面**：就绪只意味着显形"加载完成 · 点击或按任意键跳过动画"，动画继续轮换，
  用户点了/按键才退出（`leave reason=click|key`）。clip 放完**不会**把人带进界面——这一条同时修掉了
  "退场那一刻屏幕上正好有全屏 loader"的尾巴；
- **点击/按键只在就绪之后生效**：加载中点击会被忽略（否则正好落在 App 自己的加载态上、看见四象限）。
  非就绪时只剩两个出口：兜底上限 120s、以及"一整圈都加载失败"；
- **轮完一整圈都放不出来才放手**：单段 `error` 只是换下一段；连续 `playlist.length` 次失败
  （期间没有 `playing`）才淡出。注意轮换只由 `ended` 触发——**卡住但不发 `ended`** 的片段不会被跳过，
  只能等上限兜底；
- **上限 120s**（`holdMs` 给了用它，后端夹到 `120000`；缺省也是 `120000`）：只防"应用永远加载不出来，
  把用户永远挡在开屏页"这一种情况。也就意味着：App 若一直有某个四象限 loader 在转（某个面板卡住），
  动画会放到上限为止——这是刻意的权衡，点击/按键仍可立即进入；
- **自动播放被拒 / 环境没有媒体栈** → 放手（`play()` 的 `AbortError` 是有界重试，不当作"被拒"）；
- `prefers-reduced-motion: reduce` → 直接不注入（glyph 也不藏，保持默认 loader）；
- `fetch` 失败 / 无声明 → 静默不注入并恢复默认 loader。**只有首次访问**（还没有缓存）会有
  ~0.1–0.9s 的主题底色空白（不是四象限）；从第二次起就是"一访问即动画"。

### 2.4 埋点：这段启动时间花在哪

开屏脚本自带一份时间线，专门用来回答"从刷新到真正进界面，是不是全程都在放动画"：

- `window.__opensquadBoot`：事件数组，每条 `{ t, event, quads, vis, ... }`，`t` 是相对页面开始的毫秒数；
- 同一份也打 console，过滤 `[boot-screen]`；App 侧另有 `App.tsx` 的 `[boot] startup gate …` 两行（开/关）。

事件序列：`start` → `preload`（有缓存，本地先起播）→ `inject`（带 `clips` / `preloaded`）→ `playing i=N dur=`
→（`advance i=N` / `error i=N` / `play-retry`）→ `app-ready` →（`app-not-ready`）→ `leave reason=` → `ui-visible`。

三个字段是判据：

- **`vis` = 那一刻屏幕上**可见**的内置四象限个数。这是"用户到底有没有看见内置动画"的证据：静态 glyph 被
  藏掉后仍留在 DOM 里（就绪判据要用），所以只看 `quads` 会误判。**全程 `vis=0` 才算达标**；
- `quads` = 那一刻 `#root` 里**存在**几个内置四象限 loader（`svg[role="status"]` 的尺寸，`96` = 启动 gate）；
- `ui-visible` = 退场之后内置 loader 真正消失的时刻，与 `leave` 的间隔就是"动画没了但还没进界面"的露馅时长。

实测（2026-10-07，:5173，`opensquad dev` 真机重启 + 浏览器实跑）：

| 场景 | 时间线 | 结论 |
|---|---|---|
| **第二次访问（有缓存）** | `start` vis=0 → **`preload` 9ms** → `inject` 152ms（`preloaded=true`）→ `playing` 243ms → `app-ready` 502ms → 一直 `advance`（8.5s / 24.0s / 36.7s…）→ `leave click` 27.6s | **一访问即动画**（9ms），就绪后不走，等用户点 |
| 首次访问（无缓存） | `start` vis=0 → `inject` 43ms → `playing` 240ms → `app-ready` 974ms → 连续 `advance` 118s 无 `leave` | 43ms 的空窗是主题底色（**不是四象限**） |
| 慢启动（其它 `/api` 延迟 5s） | `inject` .21s → `advance` 8.8s（gate 未关 → 换段）→ `app-ready` 12.9s → `leave` 24.2s | 动画盖住整个启动窗口 |
| 把就绪按住不放 | 连续 `advance` 0→1→2→0→1→2，单圈 35.6s，60s 内无 `leave` | 启动多久就轮换多久（上限 120s） |
| **几何验证**（修掉"样式长在 #root 里"之后，登录态、覆盖 App 的 gate + agent-web 加载） | 175 次 / 200ms 采样：覆盖层恒为 `position:fixed` / `z-index:2147483000` / `top=0`，**屏幕中心最顶层元素 175/175 是 `VIDEO`**，四象限 0 次——同一时段埋点记到 App 确有 `quads=18,18,18,18`（vis=5）与 `quads=13`（vis=1）在转 | App 自己的 loader 全被盖住 |
| **无缝验证**（像素 + `readyState` 采样，修好封面之前） | 首段：`inject` 91ms → `playing` 1281ms → **首帧上屏 ~2.2s**（此前 1.3~2.2s 是底色）；换段：`advance` 9403ms（`readyState` 掉到 1、无帧）→ 新帧 9613ms，**中间 ~150ms 底色** | 两处空档实测存在，已由首帧封面消掉 |
| **封面生效**（同一页实测） | `localStorage['opensquad.bootScreen:posters']` 抓到 3 张（2.1KB / 2.0KB / 13.4KB）；两次 `advance` 埋点均为 **`cover=true`**；首屏视频 `poster` = 2127 字符的 JPEG dataURL | 首屏与换段都有画面 |
| **换段（冷缓存，`touch` 三个 mp4 让 `v=` 变新从而缓存失效）** | 三次 `advance` 埋点都是 **`pre=4`**（下一段已完全缓冲），到新帧分别 **57 / 61 / 76ms** | 换段不再停在封面上 |

**剩下的那个空窗**：只在**从未访问过**（或清了 localStorage）时存在——覆盖层要等
`/api/ai-web/boot-screen` 返回才知道播什么，这段显示的是主题底色（glyph 已藏，**不是四象限**）。
第二次起由缓存兜住，实测 9ms 上画面。

**`z-index` 必须极高**（`2147483000`）：应用自己有不透明的全屏层（`App.tsx` 的
`fixed inset-0 z-50 bg-panel`、`DesktopUpdateOverlay` 的 `z-[9999]` 等），覆盖层低于它们时动画
还在放、只是被盖住——表现就是"动画没播完就进界面了"。`bootScreen.scan.test.ts` 把这个数值钉住。

## 3. 改动清单

| 文件 | 改动 |
|---|---|
| `src/opensquad/plugin_boot_screen.py` | 纯函数：按优先级扫插件目录 → 解析 `contributes.bootScreen`（`videos`/`video`，丢掉不存在的条目）→ 产出播放列表 URL + 按 `index` 定位素材 |
| `src/opensquad/gateway/backend/app/ai_web/routes/_main.py` | 两个免鉴权端点（配置 + 受限素材；素材端点带 `index`） |
| `src/opensquad/gateway/nexuschat-pro/index.html` | `.boot-screen` 样式 + 内联加载脚本（藏静态 glyph、`localStorage['opensquad.bootScreen']` 预载、实时/双向就绪、用户手动退场、埋点） |
| `docs/plugin-boot-screen.md` | 本文 |
| `tests/test_plugin_boot_screen.py` | 解析器 / 优先级 / 受限 kind / 路由契约 / ASGI 端到端 |
| `.../utils/bootScreen.scan.test.ts` | 加载脚本的契约（reduced-motion、fail-open、挂 body、轮换、就绪信号、退出路径） |
| `.../utils/bootScreen.dom.test.ts` | 加载脚本**真跑**（jsdom） |
| `.../utils/bootReadySignal.scan.test.ts` | App↔shell 就绪属性名的一致性守卫 |
| `App.tsx` | 启动 gate 关闭时置 `<html data-opensquad-ready="1">`（+ 一行埋点） |

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
| 加载脚本**真跑**（jsdom）：注入到 body / 未就绪时 `ended` 换下一段 / 走完一圈回到第一段 / 一整圈都失败才放手 / 挂载了但还在 gate 里继续轮换 / gate 已过但 App 仍显示四象限时不退场 / **就绪后 `ended` 也不退场（用户退出才算）** / 点击 / `holdMs` / 无声明 / 请求失败 / reduced-motion / **有缓存时不等网络就起播** / **藏掉内置 glyph** / **App 回到加载态时收回提示** | `vitest utils/bootScreen.dom.test.ts` — **21 passed** |
| 加载脚本契约（含"轮换不是单条重放"、"失败满一圈才放手"、"就绪 = 标志 + 无四象限"、"退场只由用户触发"、"缓存优先"、**"覆盖层 CSS 必须在 `</head>` 之前"**、"换段先挂 poster 再换 src"、120s 上限） | `vitest utils/bootScreen.scan.test.ts` — **21 passed** |
| App↔shell 对接口径（`data-opensquad-ready` 两边同名，改一边即红） | `vitest utils/bootReadySignal.scan.test.ts` — **3 passed** |
| **埋点与端到端时间线**：真机重启 + 首访/再访、5s 慢启动、就绪按住 60s、点击退场 —— 见 §2.4 的实测表 | 浏览器里读 `window.__opensquadBoot` 与 `[boot-screen]` 日志（:5173） |
| 回归（读 `index.html` 的既有测试 + 挂在同一 router 上的 mods 路由） | 前端整棵树 `vitest run` — **152 files / 1594 passed**；后端 `test_gateway_mods_routes.py` — **13 passed** |

未验证的一步：在**跑着的实例**上肉眼看画面——需要按 §8 放一段真片并重启。代码链路已实测到
"字节能从 URL 取回 200/206"，剩下的只是浏览器解码显示。

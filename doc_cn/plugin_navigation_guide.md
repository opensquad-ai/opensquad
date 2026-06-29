# 插件侧边栏导航功能开发指南

## 概述

从 v1.0 开始，插件可以在左侧边栏注册自定义导航按钮，用户点击后可以打开插件专属的视图界面。该功能支持 Lucide 图标库和自定义图片图标，并由用户完全控制启用/禁用状态。

## 核心特性

### 1. 用户控制
- **默认关闭**：所有插件的侧边栏导航默认不显示
- **手动启用**：用户需要在插件管理界面的配置面板中手动启用
- **持久化**：用户的启用状态保存在浏览器 localStorage 中

### 2. 图标类型
- **Lucide 图标**（`iconType: 'lucide'`）：使用 Lucide 图标库的图标名称
- **自定义图片**（`iconType: 'image'`）：使用插件内置的图片文件（支持 PNG、SVG、JPG）

### 3. 实时更新
- 用户切换导航启用状态后，侧边栏立即更新
- 无需刷新页面

---

## 快速开始

### 1. 配置 plugin.json

在插件的 `plugin.json` 中添加 `contributes.navigation` 字段：

#### 使用 Lucide 图标
```json
{
  "name": "my_plugin",
  "display_name": "我的插件",
  "version": "1.0.0",
  "contributes": {
    "navigation": {
      "icon": "Zap",
      "label": "我的插件",
      "view": "my_plugin_view",
      "enabled": false,
      "iconType": "lucide"
    },
    "views": [
      {
        "name": "my_plugin_view",
        "title": "我的插件视图",
        "icon": "Zap",
        "data_endpoint": "/my/data"
      }
    ]
  }
}
```

#### 使用自定义图片图标
```json
{
  "name": "my_plugin",
  "display_name": "我的插件",
  "version": "1.0.0",
  "contributes": {
    "navigation": {
      "icon": "custom",
      "label": "我的插件",
      "view": "my_plugin_view",
      "enabled": false,
      "iconType": "image",
      "iconUrl": "/api/plugins/static/my_plugin/assets/icon.png"
    },
    "views": [
      {
        "name": "my_plugin_view",
        "title": "我的插件视图",
        "icon": "Image",
        "data_endpoint": "/my/data"
      }
    ]
  }
}
```

### 2. 准备图标文件（仅自定义图标）

如果使用自定义图片图标，需要在插件目录中创建图标文件：

```
plugins/
└── my_plugin/
    ├── plugin.json
    ├── plugin.py
    └── assets/
        └── icon.png    # 24x24 或 48x48 推荐
```

### 3. 启用导航

1. 启动 Gateway：`python -m opensquad.gateway`
2. 访问 `http://localhost:9555`
3. 进入"插件管理"页面
4. 点击插件卡片右侧的配置按钮
5. 在配置面板顶部找到"侧边栏导航"区域
6. 点击"启用导航"按钮

导航按钮将立即出现在左侧边栏！

---

## 配置字段详解

### `contributes.navigation` 对象

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `icon` | `string` | 是 | Lucide 图标名称或自定义标识 |
| `label` | `string` | 是 | 导航按钮的显示文本 |
| `view` | `string` | 是 | 关联的视图名称（必须在 `contributes.views` 中定义） |
| `enabled` | `boolean` | 否 | 默认是否启用（**应始终为 `false`**） |
| `iconType` | `'lucide' \| 'image'` | 否 | 图标类型，默认 `'lucide'` |
| `iconUrl` | `string` | 否 | 自定义图片 URL（仅 `iconType='image'` 时需要） |

### 图标 URL 格式

自定义图标的 URL 格式为：
```
/api/plugins/static/{plugin_name}/{file_path}
```

示例：
- `/api/plugins/static/my_plugin/assets/icon.png`
- `/api/plugins/static/weather_plugin/images/weather.svg`

---

## 最佳实践

### 1. 图标设计建议

#### Lucide 图标
- 优先选择语义清晰的图标（如 `Database`、`FileText`、`Calendar`）
- 避免使用过于通用的图标（如 `Box`、`Circle`）
- 参考 [Lucide Icons](https://lucide.dev/icons/) 浏览所有可用图标

#### 自定义图片图标
- **尺寸**：推荐 24x24px 或 48x48px（在侧边栏中显示为 24x24px）
- **格式**：PNG（支持透明背景）或 SVG（矢量图形）
- **颜色**：使用单色或简单配色，避免过于复杂的设计
- **透明背景**：建议使用透明背景以适应不同主题
- **文件大小**：尽量控制在 10KB 以内

### 2. 导航配置建议

- **始终设置 `enabled: false`**：让用户决定是否启用导航
- **简洁的标签**：`label` 应简短（1-4 个汉字或 5-10 个字母）
- **唯一的视图名称**：`view` 必须与 `contributes.views` 中的某个视图名称匹配

### 3. 视图开发建议

- 导航按钮点击后会打开右侧面板显示对应的视图
- 视图需要在 `contributes.views` 中定义
- 视图内容通过 `data_endpoint` 返回 JSON 数据渲染

---

## 示例插件

### 示例 1：使用 Lucide 图标

**插件结构**：
```
plugins/
└── example_nav_plugin/
    ├── plugin.json
    └── plugin.py
```

**plugin.json**：
```json
{
  "name": "example_nav_plugin",
  "display_name": "示例导航插件",
  "version": "1.0.0",
  "type": "platform",
  "enabled": true,
  "contributes": {
    "navigation": {
      "icon": "Zap",
      "label": "示例导航",
      "view": "example_view",
      "enabled": false,
      "iconType": "lucide"
    },
    "views": [
      {
        "name": "example_view",
        "title": "示例视图",
        "icon": "Zap",
        "data_endpoint": "/example/data"
      }
    ]
  }
}
```

**plugin.py**：
```python
from opensquad.plugin_system.plugin_base import PluginBase
from flask import jsonify

class ExampleNavPlugin(PluginBase):
    def handle_request(self, path, query_params, body):
        if path == "/example/data":
            return jsonify({
                "title": "示例数据",
                "content": "这是示例导航插件的内容"
            })
        return None
```

### 示例 2：使用自定义图片图标

**插件结构**：
```
plugins/
└── example_custom_icon_plugin/
    ├── plugin.json
    ├── plugin.py
    └── assets/
        └── icon.png
```

**plugin.json**：
```json
{
  "name": "example_custom_icon_plugin",
  "display_name": "自定义图标插件",
  "version": "1.0.0",
  "type": "platform",
  "enabled": true,
  "contributes": {
    "navigation": {
      "icon": "custom",
      "label": "自定义图标",
      "view": "custom_icon_view",
      "enabled": false,
      "iconType": "image",
      "iconUrl": "/api/plugins/static/example_custom_icon_plugin/assets/icon.png"
    },
    "views": [
      {
        "name": "custom_icon_view",
        "title": "自定义图标视图",
        "icon": "Image",
        "data_endpoint": "/custom/data"
      }
    ]
  }
}
```

**plugin.py**：
```python
from opensquad.plugin_system.plugin_base import PluginBase
from flask import jsonify

class ExampleCustomIconPlugin(PluginBase):
    def handle_request(self, path, query_params, body):
        if path == "/custom/data":
            return jsonify({
                "title": "自定义图标数据",
                "content": "这是自定义图标插件的内容"
            })
        return None
```

---

## 用户界面说明

### 插件管理界面

在插件配置面板中，导航控制区域显示：

- **图标预览**：显示当前配置的图标（Lucide 或自定义图片）
- **启用状态**：显示"已启用"或"已禁用"
- **切换按钮**：点击切换启用/禁用状态

### 侧边栏导航

启用后，导航按钮显示在左侧边栏：

- **图标**：显示配置的 Lucide 图标或自定义图片
- **工具提示**：鼠标悬停显示 `label` 文本
- **点击行为**：打开右侧面板显示对应的视图

---

## 技术实现细节

### 前端组件

- **Sidebar.tsx**：负责渲染导航按钮
  - 从 localStorage 读取用户启用状态
  - 监听 `plugin-nav-changed` 事件实时更新
  - 支持 Lucide 图标和自定义图片渲染

- **PluginManagerPage.tsx**：负责导航控制
  - 显示导航配置面板
  - 提供启用/禁用切换按钮
  - 保存用户偏好到 localStorage
  - 触发 `plugin-nav-changed` 事件

### 用户偏好存储

- **存储位置**：浏览器 localStorage
- **存储键**：`plugin_nav_enabled_${pluginName}`
- **存储值**：`'true'` 或 `'false'`（字符串）
- **默认值**：未设置时视为 `false`（关闭）

### 静态资源服务

- **路由**：`/api/plugins/static/*`
- **目录**：`plugins/` 根目录
- **访问方式**：`/api/plugins/static/{plugin_name}/{file_path}`

---

## 常见问题

### Q1: 为什么我的导航按钮没有显示？

**可能原因**：
1. `enabled` 字段设置为 `true`（应该是 `false`）
2. 用户未在插件管理界面启用导航
3. `view` 名称与 `contributes.views` 中的视图名称不匹配
4. 前端未重新构建（运行 `npm run build`）

### Q2: 自定义图标无法显示？

**可能原因**：
1. `iconUrl` 路径错误（应该是 `/api/plugins/static/{plugin_name}/{file_path}`）
2. 图标文件不存在或路径错误
3. 图标文件格式不支持（建议使用 PNG 或 SVG）
4. 图标文件过大或损坏

**调试方法**：
- 在浏览器开发者工具的 Network 标签页中检查图标请求
- 直接访问图标 URL 确认文件可访问

### Q3: 如何更换图标？

**Lucide 图标**：
1. 在 [Lucide Icons](https://lucide.dev/icons/) 中找到新图标
2. 修改 `plugin.json` 中的 `icon` 字段
3. 重新加载插件或刷新页面

**自定义图片**：
1. 替换 `assets/` 目录中的图标文件
2. 如果文件名不同，更新 `iconUrl` 字段
3. 重新加载插件或刷新页面（可能需要清除浏览器缓存）

### Q4: 如何禁用导航？

用户可以在插件管理界面的配置面板中点击"禁用导航"按钮。

开发者**不应该**在代码中强制禁用导航（保持 `enabled: false`，让用户控制）。

### Q5: 导航按钮的顺序如何确定？

导航按钮的顺序由插件加载顺序决定，暂不支持自定义排序。

---

## 相关文档

- [插件开发指南](./PLUGIN_DEVELOPMENT.md)
- [Lucide Icons](https://lucide.dev/icons/)

---

## 更新日志

### v1.0.0 (2026-03-01)
- 初始版本
- 支持 Lucide 图标和自定义图片图标
- 用户控制启用/禁用
- localStorage 持久化存储

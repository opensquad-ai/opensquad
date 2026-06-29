# Plugin Ecosystem

OpenSquad has two related but distinct plugin surfaces:

## 1. Built-in plugins (`src/plugins/`)

- Shipped with the main repository.
- Loaded by Launcher and Agent `PluginManager` at runtime.
- Examples: `websearch`, `whisper`, `telegram`, `git_core`.
- Configuration: `workspace/data/plugins/<name>/config.json` (runtime) + `plugin.json` schema.

## 2. Plugin Registry service (port 9720)

- HTTP API for plugin **metadata** (market listings, versions, install URLs).
- Does **not** execute plugin code — Launcher installs from Git URLs / local paths into `src/plugins/`.
- Gateway UI "Plugin Market" talks to Registry; runtime still uses Launcher + `PluginManager`.

## Data flow

```
Plugin Market UI  →  Gateway  →  Plugin Registry (metadata)
Agent / Launcher  →  src/plugins/*  →  PluginManager  →  ToolRegistry
Service plugins   →  Launcher process_manager  →  HTTP child (e.g. websearch :9001)
```

## Contributing a plugin

1. Develop under `src/plugins/<your_plugin>/` with `plugin.py` + `plugin.json`.
2. Follow [doc_en/PLUGIN_DEVELOPMENT.md](../doc_en/PLUGIN_DEVELOPMENT.md).
3. For distribution via Registry, publish metadata separately (future: opensquad-plugins org).

## Security

- Never commit API keys in `plugin.json` or `data/plugins/*/config.json`.
- Service plugins bind local ports; keep them off the public internet unless required.

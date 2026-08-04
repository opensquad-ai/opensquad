import path from 'path';
import fs from 'fs';
import os from 'os';
import { defineConfig, loadEnv, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';

/**
 * Strip modulepreload for the two heavy deferred chunks (mermaid graph
 * renderer + TipTap/ProseMirror editor). Vite adds a <link rel=modulepreload>
 * for every dynamic-import dependency, so a refresh would otherwise download
 * ~3.7MB of editor/mermaid code (it just wouldn't *execute*). Both chunks are
 * loaded on demand at runtime — chat hydrate / file-tab open fetch them when
 * actually needed.
 */
function stripDeferredChunkPreload(): Plugin {
  return {
    name: 'strip-deferred-chunk-preload',
    transformIndexHtml(html: string) {
      return html
        .replace(/<link[^>]*rel="modulepreload"[^>]*href="[^"]*vendor-mermaid-[^"]*\.js"[^>]*>\s*/g, '')
        .replace(/<link[^>]*rel="modulepreload"[^>]*href="[^"]*vendor-editor-[^"]*\.js"[^>]*>\s*/g, '');
    },
  };
}

// 查找 system_config.json：优先工作区，回退到安装目录，再回退到 example 模板，最后使用默认值
function loadSystemConfig(): { ports: Record<string, number>; hosts: Record<string, string> } {
    const defaultConfig = {
        ports: { frontend: 5173, gateway: 8000 },
        hosts: { frontend: '0.0.0.0', gateway: '0.0.0.0' }
    };

    // Helper: try a path, fallback to .example.json, return parsed config or null
    function tryLoadConfig(basePath: string): { ports: Record<string, number>; hosts: Record<string, string> } | null {
        for (const suffix of ['system_config.json', 'system_config.example.json']) {
            const fullPath = path.join(basePath, suffix);
            if (fs.existsSync(fullPath)) {
                try {
                    const data = JSON.parse(fs.readFileSync(fullPath, 'utf-8'));
                    console.log(`[vite.config] Using ${suffix} from: ${fullPath}`);
                    return data;
                } catch (_) {}
            }
        }
        return null;
    }

    // 1. 尝试从 ~/.opensquad/last_workspace.json 读取当前工作区
    try {
        const lastWsFile = path.join(os.homedir(), '.opensquad', 'last_workspace.json');
        if (fs.existsSync(lastWsFile)) {
            const wsData = JSON.parse(fs.readFileSync(lastWsFile, 'utf-8'));
            const wsPath = wsData?.last_workspace;
            if (wsPath) {
                const cfg = tryLoadConfig(wsPath);
                if (cfg) return cfg;
            }
        }
    } catch (_) {}

    // 2. 回退：安装目录
    const installDir = path.resolve(__dirname, '../../../');
    const cfg = tryLoadConfig(installDir);
    if (cfg) return cfg;

    // 3. 兜底：使用默认值
    console.warn('[vite.config] system_config.json/example not found, using default ports (frontend:5173, gateway:8000)');
    return defaultConfig;
}

function loadAppVersion(): string {
    // Source of truth: pyproject.toml. The package's `__version__` in
    // src/opensquad/__init__.py is a hand-maintained fallback used only when
    // pyproject.toml is unavailable; choosing __init__.py first invites
    // drift (the original bug — UI showed v0.1.1 long after pyproject.toml
    // had moved on to 0.3.0.dev0).
    const pyproject = path.resolve(__dirname, '../../../../pyproject.toml');
    if (fs.existsSync(pyproject)) {
        try {
            const text = fs.readFileSync(pyproject, 'utf-8');
            const match = text.match(/^version\s*=\s*["']([^"']+)["']/m);
            if (match) return match[1];
        } catch (_) {}
    }
    const initPy = path.resolve(__dirname, '../../../opensquad/__init__.py');
    if (fs.existsSync(initPy)) {
        try {
            const text = fs.readFileSync(initPy, 'utf-8');
            const match = text.match(/^__version__\s*=\s*["']([^"']+)["']/m);
            if (match) return match[1];
        } catch (_) {}
    }
    return '0.0.0';
}

const rawConfig = loadSystemConfig();

// 映射到旧的配置结构，并对缺失字段提供安全默认值
const frontendConfig = {
    port: rawConfig?.ports?.frontend ?? 5173,
    host: rawConfig?.hosts?.frontend || '0.0.0.0',
    strict_port: true
};
const backendConfig = {
    host: rawConfig?.hosts?.gateway || '127.0.0.1',
    port: rawConfig?.ports?.gateway ?? 8000
};

export default defineConfig(({ mode }) => {
    const env = loadEnv(mode, '.', '');
    // 读取环境变量（来自 .env.local），回退到 system_config.json 的值，再回退到硬编码默认
    const backendHost = env.VITE_BACKEND_HOST || (backendConfig.host === '0.0.0.0' ? '127.0.0.1' : backendConfig.host);
    const backendPort = env.VITE_BACKEND_PORT || backendConfig.port;
    const backendUrl = `http://${backendHost}:${backendPort}`;

    // Force LAN-accessible frontend binding unless explicitly overridden by env
    const frontendHost = env.VITE_FRONTEND_HOST || (frontendConfig.host === '127.0.0.1' || frontendConfig.host === 'localhost' ? '0.0.0.0' : frontendConfig.host);

    return {
      server: {
        fs: {
          allow: [
            path.resolve(__dirname),
            path.resolve(__dirname, '../../../plugins')
          ]
        },
        port: frontendConfig.port,
        host: frontendHost,
        strictPort: frontendConfig.strict_port ?? true,
        hmr: true,
        proxy: {
          // Launcher-direct routes (bypass gateway for working-directory etc.)
          '/api/launcher': {
            target: `http://${backendHost}:9600`,
            changeOrigin: true,
            rewrite: (p: string) => p.replace(/^\/api\/launcher/, ''),
          },
          '/api': {
            target: backendUrl,
            changeOrigin: true,
          },
          '/uploads': {
            target: backendUrl,
            changeOrigin: true,
          },
          // Agent WebSocket — LAN still proxies via Vite; local DEV clients
          // prefer direct :gateway (see api.ts getWsAuthority) to avoid
          // Vite WS proxy CONNECTING hangs after long uptime.
          '/ai-web': {
            target: backendUrl,
            changeOrigin: true,
            ws: true,
            timeout: 60_000,
            proxyTimeout: 60_000,
          },
        }
      },
      plugins: [react(), stripDeferredChunkPreload()],
      define: {
        'import.meta.env.VITE_APP_VERSION': JSON.stringify(loadAppVersion()),
        'process.env.API_KEY': JSON.stringify(env.GEMINI_API_KEY),
        'process.env.GEMINI_API_KEY': JSON.stringify(env.GEMINI_API_KEY)
      },
      resolve: {
        alias: {
          '@': path.resolve(__dirname, '.'),
          '@plugins': path.resolve(__dirname, '../../../plugins'),
        }
      },
      build: {
        chunkSizeWarningLimit: 500,
        modulePreload: {
          // Disable Vite's runtime modulepreload polyfill. Without this the
          // polyfill preloads EVERY chunk in the entry's __vite__mapDeps list —
          // including vendor-mermaid (3.2MB) — on first paint even when no
          // message contains a mermaid block. With it off, dynamic chunks load
          // only when actually imported (chat message with mermaid, opening a
          // file tab, navigating to a route page).
          polyfill: false,
        },
        rollupOptions: {
          output: {
            manualChunks(id) {
              // React core
              if (id.includes('node_modules/react-dom') || id.includes('node_modules/react/') || id.includes('node_modules/scheduler/')) {
                return 'vendor-react';
              }
              // i18next
              if (id.includes('node_modules/i18next') || id.includes('node_modules/react-i18next')) {
                return 'vendor-i18n';
              }
              // Marked + highlight.js
              if (id.includes('node_modules/marked') || id.includes('node_modules/highlight.js')) {
                return 'vendor-markdown';
              }
              // Lucide icons — NOT grouped into a manual chunk. Return
              // undefined so Rollup tree-shakes them into whichever chunk
              // imports them: the first-paint bundle only carries the icons
              // the chat path actually renders, and async route chunks carry
              // their own. (Forcing them into vendor-icons merged ~876KB of
              // icons into the critical path.)
              if (id.includes('node_modules/lucide-react')) {
                return undefined;
              }
              // Mermaid + its whole dependency tree (cytoscape, d3, dagre,
              // katex, dompurify, …). mermaidHydrate already loads mermaid via
              // dynamic import; grouping it here keeps the huge graph libraries
              // out of vendor-other so they are only fetched when a chat
              // message actually contains a mermaid block.
              if (
                id.includes('node_modules/mermaid')
                || id.includes('node_modules/@mermaid-js')
                || id.includes('node_modules/cytoscape')
                || id.includes('node_modules/d3')
                || id.includes('node_modules/dagre')
                || id.includes('node_modules/dompurify')
                || id.includes('node_modules/katex')
                || id.includes('node_modules/dayjs')
                || id.includes('node_modules/khroma')
                || id.includes('node_modules/non-layered-tidy-tree-layout')
                || id.includes('node_modules/@braintree')
                || id.includes('node_modules/stylis')
                || id.includes('node_modules/uqr')
                || id.includes('node_modules/ts-dedent')
                || id.includes('node_modules/he')
                || id.includes('node_modules/layout-elk')
                || id.includes('node_modules/elkjs')
                || id.includes('node_modules/@zenuml')
              ) {
                return 'vendor-mermaid';
              }
              // TipTap / ProseMirror rich-text editor — kept in vendor-other.
              // Splitting the prosemirror-* packages into their own chunk
              // breaks their cross-package circular imports at module init
              // (Fragment/Node undefined at runtime), so we do NOT split them;
              // FileDocumentEditor stays lazy but resolves against vendor-other.
              // Other dependencies
              if (id.includes('node_modules/')) {
                return 'vendor-other';
              }
            }
          }
        }
      }
    };
});

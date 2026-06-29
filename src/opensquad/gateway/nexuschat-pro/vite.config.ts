import path from 'path';
import fs from 'fs';
import os from 'os';
import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// 查找 system_config.json：优先工作区，回退到安装目录，再回退到 example 模板，最后使用默认值
function loadSystemConfig(): { ports: Record<string, number>; hosts: Record<string, string> } {
    const defaultConfig = {
        ports: { frontend: 5173, gateway: 8000 },
        hosts: { frontend: '0.0.0.0', gateway: '0.0.0.0' }
    };

    // Helper: try a path, fallback to .example.json, return parsed config or null
    function tryLoadConfig(basePath: string): Record<string, any> | null {
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
          '/api': {
            target: backendUrl,
            changeOrigin: true,
          },
          '/uploads': {
            target: backendUrl,
            changeOrigin: true,
          }
        }
      },
      plugins: [react()],
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
              // Lucide icons
              if (id.includes('node_modules/lucide-react')) {
                return 'vendor-icons';
              }
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

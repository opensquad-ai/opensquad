/**
 * 图片 / 头像 URL 工具
 *
 * 同源相对路径优先（走 Vite proxy 或 Gateway StaticFiles）。
 * 历史 Dicebear 外链会改写为本地机器人 SVG 占位，避免离线时出现破损图。
 */

/** 根据 seed 生成稳定的 HSL 背景色（不依赖外网 CDN）。 */
function seedColor(seed: string): string {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) {
    hash = (hash * 31 + seed.charCodeAt(i)) >>> 0;
  }
  const hue = hash % 360;
  return `hsl(${hue} 42% 48%)`;
}

function seedAccent(seed: string): string {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) {
    hash = (hash * 31 + seed.charCodeAt(i)) >>> 0;
  }
  const hue = (hash + 40) % 360;
  return `hsl(${hue} 55% 70%)`;
}

/** 取用于占位的 1–2 个字符（中文取首字，英文取首字母）。 */
export function avatarInitials(label: string | undefined): string {
  const text = (label || '?').trim();
  if (!text) return '?';
  // CJK / emoji: first code point
  const first = Array.from(text)[0] || '?';
  if (/[a-zA-Z0-9]/.test(first)) {
    const parts = text.split(/\s+/).filter(Boolean);
    if (parts.length >= 2) {
      return (parts[0][0] + parts[1][0]).toUpperCase();
    }
    return first.toUpperCase();
  }
  return first;
}

/**
 * 本地 SVG data-URI 占位头像（不依赖 Dicebear / 外网）。
 * seed 决定背景色；label 决定中间文字。
 */
export const getLocalAvatarFallback = (
  seed: string = 'default',
  label?: string,
): string => {
  const initial = avatarInitials(label || seed);
  const bg = seedColor(seed);
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">` +
    `<rect width="128" height="128" rx="64" fill="${bg}"/>` +
    `<text x="64" y="64" dy="0.35em" text-anchor="middle" ` +
    `font-family="system-ui,sans-serif" font-size="56" font-weight="600" fill="#fff">` +
    `${escapeXml(initial)}` +
    `</text></svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
};

/**
 * 本地机器人头像（替代历史 Dicebear bottts），按 seed 稳定着色。
 */
export const getLocalBotAvatarFallback = (seed: string = 'default'): string => {
  const bg = seedColor(seed);
  const accent = seedAccent(seed);
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">` +
    `<rect width="128" height="128" rx="24" fill="${bg}"/>` +
    `<rect x="28" y="36" width="72" height="64" rx="14" fill="#fff" fill-opacity="0.92"/>` +
    `<circle cx="50" cy="62" r="8" fill="${bg}"/>` +
    `<circle cx="78" cy="62" r="8" fill="${bg}"/>` +
    `<rect x="48" y="80" width="32" height="6" rx="3" fill="${accent}"/>` +
    `<rect x="58" y="22" width="12" height="16" rx="4" fill="#fff" fill-opacity="0.85"/>` +
    `<circle cx="64" cy="18" r="6" fill="${accent}"/>` +
    `</svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
};

function escapeXml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** Resolve chat_profile avatar across legacy (`avatar`) and UI (`chat_user_avatar`) keys. */
export function resolveChatAvatar(
  profile?: { chat_user_avatar?: string | null; avatar?: string | null } | null,
): string | null {
  if (!profile) return null;
  return profile.chat_user_avatar || profile.avatar || null;
}

/** Resolve chat_profile display name across legacy / UI keys. */
export function resolveChatName(
  profile?: { chat_user_name?: string; name?: string } | null,
): string {
  if (!profile) return '';
  return profile.chat_user_name || profile.name || '';
}

/**
 * 将头像字段解析为可加载的 URL。
 * - 空值 → 本地 SVG 占位（不再依赖 Dicebear CDN）
 * - `data:` / `blob:` → 原样返回（切勿再拼 SERVER_BASE_URL，否则 Vite decodeURI 会炸）
 * - `/uploads/...` 等相对路径 → 保持同源相对路径
 * - 指向本机后端的绝对 `/uploads` URL → 改写为相对路径
 * - 其它 http(s) 外链 → 原样返回（加载失败由 UI onError 处理）
 */
export const getAvatarUrl = (
  avatar: string | undefined,
  seed?: string,
  label?: string,
): string => {
  if (!avatar) {
    return getLocalAvatarFallback(seed || 'default', label);
  }

  if (avatar.startsWith('data:') || avatar.startsWith('blob:')) {
    return avatar;
  }

  if (avatar.startsWith('http://') || avatar.startsWith('https://')) {
    try {
      const u = new URL(avatar);
      // 历史数据里可能存了 http://localhost:9555/uploads/xxx —— 改写为同源相对路径
      if (u.pathname.startsWith('/uploads/')) {
        return `${u.pathname}${u.search}`;
      }
      // 历史默认头像依赖 Dicebear CDN；离线 / 被墙时会显示破损图。
      // 机器人风格本地 SVG，避免先闪撕裂图标再 onError，也避免退化成字母头像。
      if (
        u.hostname === 'api.dicebear.com' ||
        u.hostname.endsWith('.dicebear.com')
      ) {
        const seedParam = u.searchParams.get('seed') || seed || 'default';
        return getLocalBotAvatarFallback(seedParam);
      }
    } catch {
      // ignore parse errors, fall through
    }
    return avatar;
  }

  // 相对路径：保持相对，走 Vite proxy / Gateway 同源 StaticFiles
  return avatar.startsWith('/') ? avatar : `/${avatar}`;
};

/**
 * Prefix same-origin relative media with an absolute API base when needed.
 * Never rewrite data:/blob:/http(s) URLs — classic-mode previously turned
 * `data:image/svg+xml,...hsl(...%)` into `/data:image/...%...` and Vite's
 * decodeURI threw "URI malformed".
 */
export function toAbsoluteMediaUrl(
  url: string | null | undefined,
  baseUrl: string,
): string | null {
  if (!url) return null;
  if (
    url.startsWith('data:') ||
    url.startsWith('blob:') ||
    url.startsWith('http://') ||
    url.startsWith('https://')
  ) {
    return url;
  }
  const path = url.startsWith('/') ? url : `/${url}`;
  return `${baseUrl}${path}`;
}

/**
 * 将附件 URL 转为可请求地址。
 * 相对路径保持同源；绝对 URL 原样返回。
 */
export const getAttachmentUrl = (url: string): string => {
  if (!url) return '';
  if (url.startsWith('http://') || url.startsWith('https://')) {
    try {
      const u = new URL(url);
      if (u.pathname.startsWith('/uploads/')) {
        return `${u.pathname}${u.search}`;
      }
    } catch {
      // ignore
    }
    return url;
  }
  return url.startsWith('/') ? url : `/${url}`;
};

// @vitest-environment node
/**
 * Custom agent avatars — the UI half of a contract the gateway cannot see.
 *
 * Uploading is one click that has to change the picture in four places: the file
 * under `/uploads`, the agent's `profile.json` (→ `GET /api/agents`, which feeds
 * this page, the sidebar and the nav shortcuts), the ChatPro `users` row (→ group
 * member lists and message avatars) and, live, the panes that are already open.
 * The server side is locked by `tests/test_agent_avatar_profile.py` and
 * `tests/test_agent_avatar_upload.py`; what rots silently on this side is:
 *
 *   - the two API methods drift off the route the gateway actually serves;
 *   - the page stops mirroring the server's validation, so an oversized or
 *     non-image file is uploaded (and rejected) after the user waited for it;
 *   - the reset affordance stops asking `isUploadedAvatar`, so every agent —
 *     including ones still showing the generated default — offers to "remove" a
 *     picture the user never set;
 *   - the response stops being applied to `agents`/`detailAgent`, so the picture
 *     only appears on the next 30s poll;
 *   - `App.tsx` stops consuming `user_updated`, so an open group chat keeps the
 *     old face until it is reloaded;
 *   - a locale key is added to one file only (a missing key renders as the key).
 *
 *   R1  `adminAPI` posts/DELETEs `/ai-web/admin/agents/{name}/avatar`
 *   R2  the page mirrors the server whitelist (MIME types + size cap), and the
 *       cap is literally the same number as `_AVATAR_MAX_BYTES`
 *   R3  reset is offered only for an uploaded avatar
 *   R4  the result is applied to the list, the open detail panel, and via refetch
 *   R5  `App.tsx` patches the user map on `user_updated`
 *   R6  every `agentManager.avatar*` key the page reads exists in zh AND en
 *
 * Mutations verified (each one makes this file fail):
 *   MA1 `method: 'POST'` → `'PUT'` in uploadAgentAvatar                       → R1
 *   MA2 `AVATAR_MAX_BYTES = 2 * 1024 * 1024` → `20 * 1024 * 1024`             → R2
 *   MA3 `isCustom={isUploadedAvatar(...)}` → `isCustom={true}`                → R3
 *   MA4 drop `applyAvatarResult(name, res.profile)` from the upload handler   → R4
 *   MA5 remove the `user_updated` subscription from App.tsx                   → R5
 *   MA6 delete `avatarResetHint` from locales/en.json                         → R6
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

const PAGE = read('components/AgentManagerPage.tsx');
const API = read('services/api.ts');
const APP = read('App.tsx');
const ADMIN_PY = read('../../gateway/backend/app/ai_web/routes/_admin.py');
const zh = JSON.parse(read('locales/zh.json'));
const en = JSON.parse(read('locales/en.json'));

/** Body of a method in the `adminAPI` object literal, by name. */
function apiMethod(name: string): string {
  const start = API.indexOf(`${name}: async`);
  expect(start, `adminAPI.${name} not found`).toBeGreaterThan(-1);
  const end = API.indexOf('\n  },', start);
  return API.slice(start, end);
}

describe('agent avatars', () => {
  it('R1 — the API methods hit the routes the gateway serves', () => {
    const upload = apiMethod('uploadAgentAvatar');
    expect(upload).toMatch(/\/ai-web\/admin\/agents\/\$\{name\}\/avatar/);
    expect(upload).toContain("method: 'POST'");
    // JSON, not multipart: apiRequest() pins Content-Type: application/json and a
    // browser cannot add a multipart boundary under that header.
    expect(upload).toMatch(/JSON\.stringify\(\{ filename[\s\S]*content: base64/);

    const reset = apiMethod('resetAgentAvatar');
    expect(reset).toMatch(/\/ai-web\/admin\/agents\/\$\{name\}\/avatar/);
    expect(reset).toContain("method: 'DELETE'");
  });

  it('R2 — client-side validation mirrors the server, including the exact cap', () => {
    // Same MIME whitelist as the server's magic-byte table.
    expect(PAGE).toMatch(/AVATAR_MIME_TYPES\s*=\s*\[[^\]]*'image\/png'[^\]]*'image\/jpeg'[^\]]*'image\/webp'[^\]]*'image\/gif'[^\]]*\]/);
    expect(PAGE).toContain('accept={AVATAR_MIME_TYPES.join');
    expect(PAGE).toMatch(/AVATAR_MIME_TYPES\.includes\(file\.type\)/);
    expect(PAGE).toMatch(/file\.size\s*>\s*AVATAR_MAX_BYTES/);

    // The two constants are a mirror of each other; a change on one side only
    // means the browser accepts what the server will refuse (or vice versa).
    const jsCap = PAGE.match(/const AVATAR_MAX_BYTES = ([0-9\s*]+);/);
    expect(jsCap, 'AVATAR_MAX_BYTES not found in the page').not.toBeNull();
    const pyCap = ADMIN_PY.match(/_AVATAR_MAX_BYTES = ([0-9\s*]+)\n/);
    expect(pyCap, '_AVATAR_MAX_BYTES not found in the gateway route').not.toBeNull();
    // eslint-disable-next-line no-new-func
    const evaluate = (expr: string) => Number(new Function(`return ${expr.trim().replace(/\s+/g, '')}`)());
    expect(evaluate(jsCap![1])).toBe(evaluate(pyCap![1]));
    expect(evaluate(jsCap![1])).toBeGreaterThan(0);

    // And every format the client advertises is one the server can sniff.
    for (const ext of ['.png', '.jpg', '.gif', '.webp']) {
      expect(ADMIN_PY, `server cannot sniff ${ext}`).toContain(ext);
    }
  });

  it('R3 — reset is offered only for an uploaded avatar', () => {
    // `isUploadedAvatar` is what keeps a generated default from looking like
    // something the user can remove; three call sites (two layouts + detail panel).
    const handoffs = PAGE.match(/isCustom=\{isUploadedAvatar\(/g) || [];
    expect(handoffs).toHaveLength(3);
    // The badge is gated on the flag rather than rendered unconditionally.
    expect(PAGE).toMatch(/isCustom && !busy &&/);
    // And the predicate itself must not be replaced by a truthiness check.
    expect(read('utils/image.ts')).toMatch(/export function isUploadedAvatar/);
  });

  it('R4 — a result is applied locally instead of waiting for the 30s poll', () => {
    for (const method of ['handleAvatarFile', 'handleAvatarReset']) {
      const start = PAGE.indexOf(`const ${method}`);
      expect(start, `${method} not found`).toBeGreaterThan(-1);
      // Next sibling declaration (2-space indent) — the body itself nests deeper.
      const body = PAGE.slice(start, PAGE.indexOf('\n  const ', start + 5));
      expect(body).toContain('applyAvatarResult(');
      expect(body).toContain('void fetchAgents()');
    }
    // applyAvatarResult patches BOTH the list and the open detail panel.
    const apply = PAGE.slice(PAGE.indexOf('const applyAvatarResult'), PAGE.indexOf('const openAvatarPicker'));
    expect(apply).toContain('setAgents(');
    expect(apply).toContain('setDetailAgent(');
  });

  it('R5 — an open group chat patches the member avatar from user_updated', () => {
    expect(APP).toContain("wsService.on('user_updated'");
    const start = APP.indexOf("wsService.on('user_updated'");
    const body = APP.slice(start, APP.indexOf('});', start));
    expect(body).toMatch(/userData\.avatar/);
    expect(body).toMatch(/users:/);
    // Subscribed in the same effect that tears the other handlers down, or the
    // listener leaks across logins.
    expect(APP).toContain('unsubscribeUserUpdated();');
  });

  it('R6 — every avatar key the page reads exists in both locales', () => {
    const used = new Set(
      Array.from(PAGE.matchAll(/t\('(agentManager\.avatar[A-Za-z]*)'/g)).map(m => m[1]),
    );
    expect(used.size).toBeGreaterThanOrEqual(6);

    for (const key of used) {
      const short = key.replace('agentManager.', '');
      expect(zh.agentManager[short], `zh.agentManager.${short} missing`).toBeTruthy();
      expect(en.agentManager[short], `en.agentManager.${short} missing`).toBeTruthy();
    }
    // Placeholders have to survive translation: the notice interpolates both.
    for (const [lang, table] of [['zh', zh], ['en', en]] as const) {
      expect(table.agentManager.avatarUploaded).toContain('{{name}}');
      expect(table.agentManager.avatarUploaded).toContain('{{groups}}');
      expect(table.agentManager.avatarTooLarge, `${lang} lost the size placeholder`).toContain('{{size}}');
    }
    expect(Object.keys(zh.agentManager).sort()).toEqual(Object.keys(en.agentManager).sort());
  });
});

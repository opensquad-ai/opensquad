/**
 * Workspace-owner fences around session tabs — structural.
 *
 * `sessionWorkspaceResolution.test.ts` covers the pure lookup. This file covers
 * what that test cannot reach: that every place which *creates a session tab*
 * consults it, and that the register-without-open hole cannot come back.
 *
 * Why fences rather than a render test: AIChatPage needs the api layer, i18n,
 * the WS event bus and a populated localStorage chrome; a jsdom mount would
 * spend its budget on mocks and still would not see `chrome.openWorkspaceIds`.
 *
 *   R1  the resolver has exactly one definition, in `utils/workspaceStore`;
 *   R2  no session tab is filed under a bare `activeWorkspace.id` — the bug's
 *       shape. Tabs of other kinds (file / tasks) may still use it;
 *   R3  the send-time binding effect opens the workspace it just registered
 *       (`ensureWorkspace` alone is what left `skill` in the menu, nothing else);
 *   R4  the sidebar click resolves the owner before opening;
 *   R5  the draft-session tab of a folder-scoped new session follows its folder.
 *
 * Verified by mutation (each makes this file fail):
 *   MF1 session-tab effect back to `openContentTab(agentId, activeWorkspace.id, …)` → R2
 *   MF2 `openWorkspaceTab(agentId, ws.id)` dropped from the binding effect        → R3
 *   MF3 `resolveSessionWorkspaceId(` dropped from handleSidebarViewSession        → R4
 *   MF4 draft-session branch back to `activeWorkspace.id`                        → R5
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = path.resolve(__dirname, '..');
const read = (rel: string) => fs.readFileSync(path.join(ROOT, rel), 'utf8');

/** Comments describe the rules below and must not trip them. */
const tsCode = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
/** Collapse whitespace so a re-wrapped call still matches. */
const flat = (src: string) => src.replace(/\s+/g, ' ');

const AICHAT = tsCode(read('components/AIChatPage.tsx'));
const AICHAT_FLAT = flat(AICHAT);

/** Slice from `start` up to (not including) the next `end`. */
const between = (src: string, start: string, end: string): string => {
  const i = src.indexOf(start);
  if (i < 0) return '';
  const j = src.indexOf(end, i + start.length);
  return j < 0 ? src.slice(i) : src.slice(i, j);
};

/** Every `openContentTab(...)` call, paren-balanced. */
function openContentTabCalls(src: string): string[] {
  const out: string[] = [];
  const needle = 'openContentTab(';
  let i = src.indexOf(needle);
  while (i >= 0) {
    let depth = 0;
    let j = i + needle.length - 1;
    for (; j < src.length; j += 1) {
      const ch = src[j];
      if (ch === '(') depth += 1;
      else if (ch === ')') {
        depth -= 1;
        if (depth === 0) {
          j += 1;
          break;
        }
      }
    }
    out.push(src.slice(i, j));
    i = src.indexOf(needle, j);
  }
  return out;
}

describe('R1 — one owner rule, defined once', () => {
  it('lives in utils/workspaceStore next to the chrome primitives', () => {
    const store = tsCode(read('utils/workspaceStore.ts'));
    expect(store).toMatch(/export function resolveSessionWorkspaceId\(/);
  });

  it('AIChatPage imports it instead of re-deriving the rule', () => {
    expect(AICHAT).toMatch(/resolveSessionWorkspaceId,/);
    // A second copy of the lookup would drift from the store's path rules.
    expect(AICHAT).not.toMatch(/function resolveSessionWorkspaceId\(/);
  });
});

describe('R2 — no session tab is filed under a bare active workspace', () => {
  const calls = openContentTabCalls(AICHAT).map(flat);
  const sessionCalls = calls.filter((c) => c.includes("kind: 'session'"));

  it('finds the session-tab call sites', () => {
    // draft / sidebar / event-bus / pending-flush — losing one silently
    // reintroduces the hole for that entry point only.
    expect(sessionCalls.length).toBeGreaterThanOrEqual(4);
  });

  it('every session tab names its owner workspace', () => {
    for (const call of sessionCalls) {
      expect(call, `session tab filed by the active workspace: ${call}`).not.toMatch(
        /openContentTab\(\s*agentId,\s*activeWorkspace\??\.id/,
      );
    }
  });

  it('still files files / task tabs under the active workspace', () => {
    // The rule is about *ownership*, not about banning the active workspace.
    const others = calls.filter((c) => !c.includes("kind: 'session'"));
    expect(
      others.some((c) => /openContentTab\(\s*agentId,\s*activeWorkspace\.id/.test(c)),
    ).toBe(true);
  });
});

describe('R3 — registering a workspace also opens it', () => {
  it('the send-time binding effect opens the workspace it just registered', () => {
    // `ensureWorkspace` writes the registry only; `openWorkspaceTab` is the one
    // that adds the id to `chrome.openWorkspaceIds` and mints a layout.
    expect(AICHAT_FLAT).toMatch(
      /const ws = ensureWorkspace\(agentId, path\); setSessionWorkspaceId\(agentId, currentSessionId, ws\.id, path\); openWorkspaceTab\(agentId, ws\.id\);/,
    );
  });

  it('renders the new tab through the store event, not a local refresh call', () => {
    // `openWorkspaceTab` saves through the store, which emits
    // WORKSPACES_CHANGED_EVENT and the listener below re-reads the snapshot.
    // (A local refreshWsSnap() here would reference a const declared later in
    // the component — TS2448 — which is why the event is the contract.)
    const body = between(
      AICHAT_FLAT,
      'const ws = ensureWorkspace(agentId, path);',
      'pendingProjectPathRef.current = null;',
    );
    expect(body).toContain('openWorkspaceTab(agentId, ws.id)');
    expect(body).not.toContain('refreshWsSnap');
    const store = tsCode(read('utils/workspaceStore.ts'));
    expect(store).toContain("WORKSPACES_CHANGED_EVENT = 'opensquad-workspaces-changed'");
    expect(AICHAT_FLAT).toContain('window.addEventListener(WORKSPACES_CHANGED_EVENT, onCh)');
  });
});

describe('R4 — a sidebar click reveals the session’s own project', () => {
  const body = flat(
    between(
      AICHAT,
      'const handleSidebarViewSession = ',
      'const handleNewSessionInWorkspace = ',
    ),
  );

  it('resolves the owner and brings it forward', () => {
    expect(body).toContain('resolveSessionWorkspaceId(');
    expect(body).toContain('openWorkspaceTab(agentId, wsId)');
  });

  it('drops a pane id that belongs to the workspace being left', () => {
    // A stale pane id would otherwise be handed to another workspace's layout.
    expect(body).toContain('const pane = sameWorkspace ? focusedPaneId : null;');
  });
});

describe('R5 — a folder-scoped new session follows its folder', () => {
  it('files the draft tab under the workspace owning boundPath', () => {
    const draft = between(
      AICHAT_FLAT,
      'const ownerId = (boundPath',
      "{ kind: 'session', id: draftSid }",
    );
    expect(draft).toContain('resolveSessionWorkspaceId(wsSnap.workspaces, { projectPath: boundPath })');
    expect(draft).toContain('openWorkspaceTab(agentId, ownerId)');
  });
});

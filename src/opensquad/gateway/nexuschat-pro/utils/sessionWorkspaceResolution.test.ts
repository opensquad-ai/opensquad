/**
 * Which workspace does a session belong to?
 *
 * The reported symptom: a workspace the user created and ran a session in shows
 * up in the `+` menu but never as a tab. The tab strip renders
 * `chrome.openWorkspaceIds` while the menu renders the whole registry, so
 * "registered but never opened" is exactly the gap. These cases pin down the
 * owner lookup that decides *which* workspace a session tab must be filed
 * under — the piece that lets the caller open the right one.
 */
import { describe, expect, it } from 'vitest';
import { resolveSessionWorkspaceId, type Workspace } from './workspaceStore';

const ws = (id: string, rootPath: string): Workspace => ({
  id,
  name: id,
  rootPath,
  createdAt: 0,
});

const SPACES: Workspace[] = [
  ws('ds', 'C:/ai_test/ds'),
  ws('skill', 'C:/Users/adminuser/Desktop/战略/ai/skill'),
  ws('runtime', 'C:\\ai_work\\pro0\\opensquad_runtime_deploy'),
];

describe('resolveSessionWorkspaceId', () => {
  it('prefers the workspace the session is bound to', () => {
    expect(
      resolveSessionWorkspaceId(SPACES, { workspaceId: 'skill', projectPath: 'C:/ai_test/ds' }),
    ).toBe('skill');
  });

  it('degrades to the project path when the bound id is gone', () => {
    // The workspace was removed here, or this origin merged a registry that no
    // longer carries it — the id alone must not strand the session.
    expect(
      resolveSessionWorkspaceId(SPACES, { workspaceId: 'ghost', projectPath: 'C:/ai_test/ds' }),
    ).toBe('ds');
  });

  it('matches the project path across separators, case and a trailing slash', () => {
    expect(resolveSessionWorkspaceId(SPACES, { projectPath: 'c:\\ai_test\\ds\\' })).toBe('ds');
    expect(
      resolveSessionWorkspaceId(SPACES, { projectPath: 'C:/AI_WORK/pro0/opensquad_runtime_deploy' }),
    ).toBe('runtime');
  });

  it('returns null when nothing resolves, so the caller keeps the active workspace', () => {
    expect(
      resolveSessionWorkspaceId(SPACES, { workspaceId: 'ghost', projectPath: 'C:/nope' }),
    ).toBeNull();
    expect(resolveSessionWorkspaceId(SPACES, {})).toBeNull();
    expect(resolveSessionWorkspaceId(SPACES, { workspaceId: '   ', projectPath: '  ' })).toBeNull();
    expect(resolveSessionWorkspaceId(SPACES, null)).toBeNull();
    expect(resolveSessionWorkspaceId(SPACES, undefined)).toBeNull();
    expect(resolveSessionWorkspaceId([], { projectPath: 'C:/ai_test/ds' })).toBeNull();
  });

  it('never invents a workspace for an unregistered folder', () => {
    // A session run in a folder that is *not* a workspace must not fabricate
    // one here — registering is `ensureWorkspace`'s job, not this lookup's.
    expect(
      resolveSessionWorkspaceId(SPACES, { projectPath: 'C:/Users/adminuser/Desktop/战略/ai' }),
    ).toBeNull();
  });

  it('is a prefix-safe match, not a substring one', () => {
    // `C:/ai_test/ds2` must not resolve to `C:/ai_test/ds`.
    expect(resolveSessionWorkspaceId(SPACES, { projectPath: 'C:/ai_test/ds2' })).toBeNull();
  });
});

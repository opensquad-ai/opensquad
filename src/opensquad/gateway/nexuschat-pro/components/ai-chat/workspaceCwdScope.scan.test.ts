/**
 * The workspace switch must scope the working directory to the session that is asking.
 *
 * Both ends already took a session id: `setWorkingDirectory(name, path, sessionId?)` sends
 * `session_id`, and the launcher writes `.session_cwd.<key>` instead of the agent-level file when it
 * arrives. The call site was the half that did not send one, so every switch wrote the shared file
 * and re-rooted every other pane — which is how a question asked in one project came back answered
 * against another's directory, and how two workspaces kept flipping one agent.
 */
import fs from 'fs';
import path from 'path';
import { describe, expect, it } from 'vitest';

const read = (rel: string) => fs.readFileSync(path.resolve(__dirname, rel), 'utf8');

const PAGE = read('../AIChatPage.tsx');
const API = read('../../services/api.ts');

describe('setting the working directory', () => {
  it('passes the session that is asking', () => {
    expect(PAGE).toContain(
      'adminAPI.setWorkingDirectory(dirName, path, currentSessionId || undefined)',
    );
  });

  it('does it from every call site, not just the workspace switch', () => {
    // The remaining four wrote the shared agent-level file, which every session without a file of
    // its own then reads — so a folder picked anywhere re-rooted the others.
    const calls = PAGE.match(/adminAPI\.setWorkingDirectory\([^;]*/g) || [];

    expect(calls.length).toBeGreaterThanOrEqual(6);
    for (const call of calls) {
      expect(call).toMatch(/(currentSessionId \|\| undefined|\bsid\b|sid\))/);
    }
  });

  it('re-applies when the session changes, so each one writes its own value', () => {
    const effect = PAGE.slice(
      PAGE.indexOf('setAgentCwd((prev) =>'),
      PAGE.indexOf('}, [activeWorkspace?.id, activeWorkspace?.rootPath, agentId, agentProfile?.dir_name, currentSessionId]);'),
    );

    expect(effect.length).toBeGreaterThan(0);
    expect(effect).toContain('adminAPI.setWorkingDirectory');
  });

  it('the client sends the id, and omits it rather than sending an empty one', () => {
    expect(API).toContain('setWorkingDirectory: async (name: string, path: string, sessionId?: string)');
    expect(API).toContain("body: JSON.stringify(sid ? { path, session_id: sid } : { path })");
  });
});

/**
 * A new session belongs to the workspace the operator is looking at.
 *
 * Reported: a session started while viewing `C:/ai_work/pro0/1003/raven` came up with the agent's
 * own directory as its cwd instead. The path handed to `handleNewSession` came from the chrome's
 * active workspace, which can be a different one — and an empty path is read downstream as "use the
 * agent's default", so the session ran in the repository the agent was installed from.
 *
 * The composer's own footer shows `agentCwd || defaultCwd`, and agentCwd follows the workspace, so
 * that is the value to anchor to. The chrome's active workspace stays as the last resort.
 */
import fs from 'fs';
import path from 'path';
import { describe, expect, it } from 'vitest';

const PAGE = fs.readFileSync(path.resolve(__dirname, '../AIChatPage.tsx'), 'utf8');

const handler = PAGE.slice(
  PAGE.indexOf('const handleNewSessionInWorkspace'),
  PAGE.indexOf('const handleSplitPane'),
);

describe('starting a session from the sidebar, the tab bar or the welcome row', () => {
  it('anchors to the workspace on screen before the chrome active one', () => {
    expect(handler).toContain(
      "const path = (projectPath || agentCwd || defaultCwd || activeWorkspace?.rootPath || '').trim();",
    );
  });

  it('does not let an empty path through as the first choice', () => {
    // The regression: `activeWorkspace?.rootPath` alone, which is undefined while looking at another
    // workspace — and undefined is what made the session fall back to the agent's own directory.
    expect(handler).not.toContain("const path = (projectPath || activeWorkspace?.rootPath || '').trim();");
  });

  it('hands the resolved path on, so the downstream fallback is never reached', () => {
    expect(handler).toContain('handleNewSession(path || undefined);');
  });
});

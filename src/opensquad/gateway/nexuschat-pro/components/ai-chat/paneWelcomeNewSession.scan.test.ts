/**
 * The pane's welcome offers a session to start, not only the views to open.
 *
 * It is a row of its own rather than an entry in PANE_VIEWS: the tab bar and the files panel render
 * that table too, and a session is not a view, so putting it there would have it turn up among the
 * views in both of them. It reuses the same handler the tab bar's + button does.
 */
import fs from 'fs';
import path from 'path';
import { describe, expect, it } from 'vitest';

import { PANE_VIEW_IDS } from '../../utils/paneViews';

const SHELL = fs.readFileSync(path.resolve(__dirname, 'WorkspacePaneShell.tsx'), 'utf8');

describe('the welcome list', () => {
  it('offers a new session, first among the rows', () => {
    expect(SHELL).toContain('data-testid="pane-welcome-new-session"');
    expect(SHELL).toContain("t('aiChat.views.newSession')");
    expect(SHELL).toContain("t('aiChat.views.newSessionHint')");
    expect(SHELL.indexOf('pane-welcome-new-session')).toBeLessThan(SHELL.indexOf('PANE_VIEWS.map'));
  });

  it('is wired to the same action the tab bar uses', () => {
    expect(SHELL).toMatch(/handlers\.onFocus\(\);\s*\n\s*handlers\.onNewSession\(\);/);
  });

  it('does not turn up among the views of the tab bar and the files panel', () => {
    expect(PANE_VIEW_IDS).not.toContain('newSession');
    expect(PANE_VIEW_IDS).not.toContain('session');
  });

  it('claims no shortcut, because none is bound to it', () => {
    const row = SHELL.slice(
      SHELL.indexOf('data-testid="pane-welcome-new-session"'),
      SHELL.indexOf('PANE_VIEWS.map'),
    );

    expect(row).not.toContain('<kbd');
  });

  it('is translated in both locales, differently', () => {
    const zh = JSON.parse(
      fs.readFileSync(path.resolve(__dirname, '../../locales/zh.json'), 'utf8'),
    );
    const en = JSON.parse(
      fs.readFileSync(path.resolve(__dirname, '../../locales/en.json'), 'utf8'),
    );

    for (const key of ['newSession', 'newSessionHint']) {
      expect(zh.aiChat.views[key]).toBeTruthy();
      expect(en.aiChat.views[key]).toBeTruthy();
      expect(zh.aiChat.views[key]).not.toBe(en.aiChat.views[key]);
    }
    expect(zh.aiChat.views.newSession).toMatch(/[\u4e00-\u9fff]/);
  });
});

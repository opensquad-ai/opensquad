import { test, expect } from '@playwright/test';
import { clearSession, enterOrCreateGroup, loginAsSmokeUser } from './helpers';

test.describe('critical path @smoke', () => {
  test.beforeEach(async ({ page }) => {
    await clearSession(page);
  });

  test('login shows group chat list @smoke', async ({ page }) => {
    await loginAsSmokeUser(page);
    await expect(page.getByTestId('chat-list')).toBeVisible();
  });

  test('enter group opens chat window @smoke', async ({ page }) => {
    await loginAsSmokeUser(page);
    await enterOrCreateGroup(page);
    await expect(page.getByTestId('chat-window')).toBeVisible();
  });
});

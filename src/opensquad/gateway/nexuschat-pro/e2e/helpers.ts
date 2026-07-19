import { expect, type Page } from '@playwright/test';

export const E2E_EMAIL = process.env.E2E_EMAIL || 'ss@ss';
export const E2E_PASSWORD = process.env.E2E_PASSWORD || 'ssssss';

/** Clear auth/session so AuthScreen is shown. */
export async function clearSession(page: Page) {
  await page.addInitScript(() => {
    try {
      localStorage.clear();
      sessionStorage.clear();
    } catch {
      /* ignore */
    }
  });
}

/** Log in via AuthScreen and wait for the group chat list. */
export async function loginAsSmokeUser(page: Page) {
  await page.goto('/');
  await expect(page.getByTestId('auth-email')).toBeVisible({ timeout: 30_000 });
  await page.getByTestId('auth-email').fill(E2E_EMAIL);
  await page.getByTestId('auth-password').fill(E2E_PASSWORD);
  await page.getByTestId('auth-submit').click();
  await expect(page.getByTestId('chat-list')).toBeVisible({ timeout: 30_000 });
}

/** Open first group, or create one if the list is empty. */
export async function enterOrCreateGroup(page: Page) {
  const items = page.getByTestId('chat-list-item');
  if ((await items.count()) === 0) {
    await page.getByTestId('chat-list-create').click();
    await page.getByTestId('chat-list-create-name').fill(`e2e-${Date.now()}`);
    await page.getByTestId('chat-list-create-submit').click();
  } else {
    await items.first().click();
  }
  await expect(page.getByTestId('chat-window')).toBeVisible({ timeout: 30_000 });
}

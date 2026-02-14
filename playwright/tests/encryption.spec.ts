import { test, expect } from '@playwright/test';
import { UnlockPopupPage, HeaderComponent } from '../pages/index.js';
import * as fs from 'fs';
import * as path from 'path';

const TEST_PASSWORD = 'testpass123';
const CONFIG_PATH = process.env.ROADMAP_CONFIG_PATH || path.resolve(__dirname, '..', 'config.test.yaml');

const CLEAN_CONFIG = `neo4j:
  uri: bolt://localhost:7688
  username: neo4j
  password: testpassword
  database: neo4j

repositories: []
ai_providers: []
ai_tasks: []
encryption_salt: null
`;

test.describe('Encryption & Lock/Unlock', () => {
  test.beforeEach(async ({ page }) => {
    // Reset config to clean (unencrypted) state and lock
    fs.writeFileSync(CONFIG_PATH, CLEAN_CONFIG);
    await page.request.post('/api/encryption/lock');
  });

  test('should show unlock popup on first visit when locked', async ({ page }) => {
    await page.goto('/');
    const popup = new UnlockPopupPage(page);
    await expect(popup.overlay).toBeVisible();
  });

  test('should show first-time warning when no encrypted fields exist', async ({ page }) => {
    await page.goto('/');
    const popup = new UnlockPopupPage(page);
    await expect(popup.overlay).toBeVisible();
    await expect(popup.firstTimeWarning).toBeVisible();
  });

  test('should show blinking lock icon in header when locked', async ({ page }) => {
    await page.goto('/');
    const header = new HeaderComponent(page);
    await expect(header.lockIcon).toBeVisible();
  });

  test('should unlock successfully with password', async ({ page }) => {
    await page.goto('/');
    const popup = new UnlockPopupPage(page);
    await popup.unlock(TEST_PASSWORD);
    await expect(popup.overlay).not.toBeVisible();
  });

  test('should hide lock icon after unlock', async ({ page }) => {
    await page.goto('/');
    const popup = new UnlockPopupPage(page);
    const header = new HeaderComponent(page);

    await expect(header.lockIcon).toBeVisible();
    await popup.unlock(TEST_PASSWORD);
    await expect(header.lockIcon).not.toBeVisible();
  });

  test('should show error for wrong password when encrypted fields exist', async ({ page }) => {
    // First unlock to create encrypted fields, then save settings, then lock
    await page.request.post('/api/encryption/unlock', { data: { password: TEST_PASSWORD } });
    // Save to encrypt fields
    const settings = await page.request.get('/api/settings');
    const config = await settings.json();
    await page.request.put('/api/settings', { data: config });
    // Lock again
    await page.request.post('/api/encryption/lock');

    // Now try wrong password
    await page.goto('/');
    const popup = new UnlockPopupPage(page);
    await popup.attemptUnlock('wrongpassword');
    await expect(popup.error).toBeVisible();
    const errorText = await popup.getErrorText();
    expect(errorText).toContain('Invalid password');
  });
});

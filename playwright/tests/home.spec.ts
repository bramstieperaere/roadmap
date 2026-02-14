import { test, expect } from '@playwright/test';
import { HomePage } from '../pages/index.js';
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

test.describe('Home Page', () => {
  let homePage: HomePage;

  test.beforeEach(async ({ page }) => {
    // Reset config and unlock
    fs.writeFileSync(CONFIG_PATH, CLEAN_CONFIG);
    await page.request.post('/api/encryption/unlock', { data: { password: TEST_PASSWORD } });

    homePage = new HomePage(page);
    await homePage.goto();
  });

  test('should display the home page', async () => {
    expect(await homePage.isVisible()).toBe(true);
  });

  test('should show the correct title', async () => {
    expect(await homePage.getTitleText()).toBe('Roadmap');
  });

  test('should show the subtitle', async () => {
    expect(await homePage.getSubtitleText()).toContain('Software project documentation');
  });

  test('should display the header with brand', async () => {
    expect(await homePage.header.isVisible()).toBe(true);
    expect(await homePage.header.getBrandText()).toContain('Roadmap');
  });

  test('should navigate to settings via "Open Settings" button', async ({ page }) => {
    await homePage.clickOpenSettings();
    await expect(page).toHaveURL(/\/settings/);
  });

  test('should navigate to settings via header cog icon', async ({ page }) => {
    await homePage.header.navigateToSettings();
    await expect(page).toHaveURL(/\/settings/);
  });

  test('should navigate back home via header brand', async ({ page }) => {
    await homePage.header.navigateToSettings();
    await expect(page).toHaveURL(/\/settings/);
    await homePage.header.clickBrand();
    await expect(page).toHaveURL(/\/$/);
  });
});

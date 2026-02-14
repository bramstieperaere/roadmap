import { test, expect } from '@playwright/test';
import { SettingsPage } from '../pages/index.js';
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

test.describe('AI Settings', () => {
  let settingsPage: SettingsPage;

  test.beforeEach(async ({ page }) => {
    // Reset config and unlock
    fs.writeFileSync(CONFIG_PATH, CLEAN_CONFIG);
    await page.request.post('/api/encryption/unlock', { data: { password: TEST_PASSWORD } });
    settingsPage = new SettingsPage(page);
    await settingsPage.goto();
  });

  test.describe('AI Providers', () => {
    test('should show empty state when no AI providers', async () => {
      await expect(settingsPage.noProvidersMessage).toBeVisible();
    });

    test('should show AI Providers card', async () => {
      await expect(settingsPage.aiProvidersCard).toBeVisible();
    });

    test('should add an AI provider', async () => {
      await settingsPage.addAIProvider();
      await expect(settingsPage.getAIProviderBlock(0)).toBeVisible();
      await expect(settingsPage.noProvidersMessage).not.toBeVisible();
    });

    test('should set AI provider fields', async () => {
      await settingsPage.addAIProvider();
      await settingsPage.setAIProviderName(0, 'OpenAI');
      await settingsPage.setAIProviderUrl(0, 'https://api.openai.com/v1');
      await settingsPage.setAIProviderKey(0, 'sk-test-key-123');
      await settingsPage.setAIProviderModel(0, 'gpt-4o');

      await expect(settingsPage.getAIProviderNameInput(0)).toHaveValue('OpenAI');
      await expect(settingsPage.getAIProviderUrlInput(0)).toHaveValue('https://api.openai.com/v1');
      await expect(settingsPage.getAIProviderKeyInput(0)).toHaveValue('sk-test-key-123');
      await expect(settingsPage.getAIProviderModelInput(0)).toHaveValue('gpt-4o');
    });

    test('should remove an AI provider', async () => {
      await settingsPage.addAIProvider();
      await expect(settingsPage.getAIProviderBlock(0)).toBeVisible();
      await settingsPage.removeAIProvider(0);
      await expect(settingsPage.noProvidersMessage).toBeVisible();
    });

    test('should persist AI provider after save and reload', async () => {
      await settingsPage.addAIProvider();
      await settingsPage.setAIProviderName(0, 'TestProvider');
      await settingsPage.setAIProviderUrl(0, 'https://test.example.com/v1');
      await settingsPage.setAIProviderModel(0, 'test-model');

      await settingsPage.save();
      await settingsPage.waitForAlert();

      // Reload and verify
      await settingsPage.goto();
      await expect(settingsPage.getAIProviderNameInput(0)).toHaveValue('TestProvider');
      await expect(settingsPage.getAIProviderUrlInput(0)).toHaveValue('https://test.example.com/v1');
      await expect(settingsPage.getAIProviderModelInput(0)).toHaveValue('test-model');
    });
  });

  test.describe('AI Tasks', () => {
    test('should show empty state when no AI tasks', async () => {
      await expect(settingsPage.noTasksMessage).toBeVisible();
    });

    test('should show AI Tasks card', async () => {
      await expect(settingsPage.aiTasksCard).toBeVisible();
    });

    test('should add an AI task', async () => {
      await settingsPage.addAITask();
      await expect(settingsPage.getAITaskBlock(0)).toBeVisible();
      await expect(settingsPage.noTasksMessage).not.toBeVisible();
    });

    test('should have repository_analysis as default task type', async () => {
      await settingsPage.addAITask();
      await expect(settingsPage.getAITaskTypeSelect(0)).toHaveValue('repository_analysis');
    });

    test('should remove an AI task', async () => {
      await settingsPage.addAITask();
      await expect(settingsPage.getAITaskBlock(0)).toBeVisible();
      await settingsPage.removeAITask(0);
      await expect(settingsPage.noTasksMessage).toBeVisible();
    });

    test('should show provider options from configured AI providers', async () => {
      // Add a provider first
      await settingsPage.addAIProvider();
      await settingsPage.setAIProviderName(0, 'MyProvider');

      // Add a task and check dropdown
      await settingsPage.addAITask();
      const providerSelect = settingsPage.getAITaskProviderSelect(0);
      const options = providerSelect.locator('option');
      // Should have "-- Select Provider --" and "MyProvider"
      await expect(options).toHaveCount(2);
    });

    test('should persist AI task after save and reload', async () => {
      // Add provider, then task pointing to it
      await settingsPage.addAIProvider();
      await settingsPage.setAIProviderName(0, 'SavedProvider');
      await settingsPage.addAITask();
      await settingsPage.setAITaskProvider(0, 'SavedProvider');

      await settingsPage.save();
      await settingsPage.waitForAlert();

      // Reload and verify
      await settingsPage.goto();
      await expect(settingsPage.getAITaskTypeSelect(0)).toHaveValue('repository_analysis');
      await expect(settingsPage.getAITaskProviderSelect(0)).toHaveValue('SavedProvider');
    });
  });
});

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

test.describe('Settings Page', () => {
  let settingsPage: SettingsPage;

  test.beforeEach(async ({ page }) => {
    // Reset config and unlock
    fs.writeFileSync(CONFIG_PATH, CLEAN_CONFIG);
    await page.request.post('/api/encryption/unlock', { data: { password: TEST_PASSWORD } });

    settingsPage = new SettingsPage(page);
    await settingsPage.goto();
  });

  test('should display the settings page', async () => {
    expect(await settingsPage.isVisible()).toBe(true);
  });

  test('should display the header', async () => {
    expect(await settingsPage.header.isVisible()).toBe(true);
  });

  test.describe('Neo4j Connection', () => {
    test('should show Neo4j configuration card', async () => {
      await expect(settingsPage.neo4jCard).toBeVisible();
    });

    test('should display default Neo4j URI', async () => {
      const uri = await settingsPage.getNeo4jUri();
      expect(uri).toContain('bolt://');
    });

    test('should display default database name', async () => {
      const db = await settingsPage.getNeo4jDatabase();
      expect(db).toBe('neo4j');
    });

    test('should allow editing Neo4j URI', async () => {
      await settingsPage.setNeo4jUri('bolt://custom-host:7687');
      expect(await settingsPage.getNeo4jUri()).toBe('bolt://custom-host:7687');
    });

    test('should test connection to Neo4j successfully', async () => {
      await settingsPage.clickTestConnection();
      const alertText = await settingsPage.waitForAlert();
      expect(alertText).toContain('Connected to Neo4j successfully');
      expect(await settingsPage.isAlertSuccess()).toBe(true);
    });

    test('should show error for invalid Neo4j connection', async () => {
      // Save the bad URI so the backend uses it for test-connection
      await settingsPage.setNeo4jUri('bolt://nonexistent:9999');
      await settingsPage.save();
      await settingsPage.waitForAlert();
      // Wait for the success alert to auto-dismiss
      await settingsPage.alert.waitFor({ state: 'hidden', timeout: 6000 });

      // Now test connection — should fail
      await settingsPage.clickTestConnection();
      await settingsPage.waitForAlert();
      expect(await settingsPage.isAlertDanger()).toBe(true);
    });
  });

  test.describe('Repositories', () => {
    test('should show empty state when no repositories', async () => {
      await expect(settingsPage.noReposMessage).toBeVisible();
    });

    test('should add a repository', async () => {
      await settingsPage.addRepository();
      await expect(settingsPage.getRepoBlock(0)).toBeVisible();
      await expect(settingsPage.noReposMessage).not.toBeVisible();
    });

    test('should set repository path', async () => {
      await settingsPage.addRepository();
      await settingsPage.setRepoPath(0, 'C:/projects/my-app');
      const input = settingsPage.getRepoPathInput(0);
      await expect(input).toHaveValue('C:/projects/my-app');
    });

    test('should remove a repository', async () => {
      await settingsPage.addRepository();
      await expect(settingsPage.getRepoBlock(0)).toBeVisible();
      await settingsPage.removeRepository(0);
      await expect(settingsPage.noReposMessage).toBeVisible();
    });

    test('should add multiple repositories', async () => {
      await settingsPage.addRepository();
      await settingsPage.addRepository();
      await expect(settingsPage.getRepoBlock(0)).toBeVisible();
      await expect(settingsPage.getRepoBlock(1)).toBeVisible();
    });

    test('should show analyze button on repository', async () => {
      await settingsPage.addRepository();
      await expect(settingsPage.getAnalyzeRepoButton(0)).toBeVisible();
    });
  });

  test.describe('Modules', () => {
    test.beforeEach(async () => {
      await settingsPage.addRepository();
    });

    test('should add a module to a repository', async () => {
      await settingsPage.addModule(0);
      await expect(settingsPage.getModuleRow(0, 0)).toBeVisible();
    });

    test('should set module name', async () => {
      await settingsPage.addModule(0);
      await settingsPage.setModuleName(0, 0, 'core-service');
      await expect(settingsPage.getModuleNameInput(0, 0)).toHaveValue('core-service');
    });

    test('should set module type to angular', async () => {
      await settingsPage.addModule(0);
      await settingsPage.setModuleType(0, 0, 'angular');
      await expect(settingsPage.getModuleTypeSelect(0, 0)).toHaveValue('angular');
    });

    test('should set module relative path', async () => {
      await settingsPage.addModule(0);
      await settingsPage.setModulePath(0, 0, 'modules/core');
      await expect(settingsPage.getModulePathInput(0, 0)).toHaveValue('modules/core');
    });

    test('should remove a module', async () => {
      await settingsPage.addModule(0);
      await expect(settingsPage.getModuleRow(0, 0)).toBeVisible();
      await settingsPage.removeModule(0, 0);
      await expect(settingsPage.getModuleRow(0, 0)).not.toBeVisible();
    });

    test('should add multiple modules', async () => {
      await settingsPage.addModule(0);
      await settingsPage.addModule(0);
      await expect(settingsPage.getModuleRow(0, 0)).toBeVisible();
      await expect(settingsPage.getModuleRow(0, 1)).toBeVisible();
    });
  });

  test.describe('Save and Persist', () => {
    test('should save settings and show success alert', async () => {
      await settingsPage.save();
      const alertText = await settingsPage.waitForAlert();
      expect(alertText).toContain('Settings saved successfully');
      expect(await settingsPage.isAlertSuccess()).toBe(true);
    });

    test('should persist Neo4j settings after save and reload', async ({ page }) => {
      await settingsPage.setNeo4jUri('bolt://test-host:7687');
      await settingsPage.setNeo4jDatabase('testdb');
      await settingsPage.save();
      await settingsPage.waitForAlert();

      // Reload and verify persistence
      await settingsPage.goto();
      expect(await settingsPage.getNeo4jUri()).toBe('bolt://test-host:7687');
      expect(await settingsPage.getNeo4jDatabase()).toBe('testdb');
    });

    test('should persist repository and module config after save and reload', async ({ page }) => {
      await settingsPage.addRepository();
      await settingsPage.setRepoPath(0, 'C:/repos/my-project');
      await settingsPage.addModule(0);
      await settingsPage.setModuleName(0, 0, 'backend-api');
      await settingsPage.setModuleType(0, 0, 'java');
      await settingsPage.setModulePath(0, 0, 'backend');

      await settingsPage.save();
      await settingsPage.waitForAlert();

      // Reload and verify
      await settingsPage.goto();
      await expect(settingsPage.getRepoPathInput(0)).toHaveValue('C:/repos/my-project');
      await expect(settingsPage.getModuleNameInput(0, 0)).toHaveValue('backend-api');
      await expect(settingsPage.getModuleTypeSelect(0, 0)).toHaveValue('java');
      await expect(settingsPage.getModulePathInput(0, 0)).toHaveValue('backend');
    });
  });
});

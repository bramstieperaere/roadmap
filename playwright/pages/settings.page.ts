import { type Page, type Locator, expect } from '@playwright/test';
import { HeaderComponent } from './header.page.js';

export class SettingsPage {
  readonly page: Page;
  readonly header: HeaderComponent;
  readonly container: Locator;

  // Neo4j fields
  readonly neo4jCard: Locator;
  readonly neo4jUri: Locator;
  readonly neo4jDatabase: Locator;
  readonly neo4jUsername: Locator;
  readonly neo4jPassword: Locator;
  readonly testConnectionButton: Locator;

  // Repositories
  readonly repositoriesCard: Locator;
  readonly addRepositoryButton: Locator;
  readonly noReposMessage: Locator;

  // AI Providers
  readonly aiProvidersCard: Locator;
  readonly addAIProviderButton: Locator;
  readonly noProvidersMessage: Locator;

  // AI Tasks
  readonly aiTasksCard: Locator;
  readonly addAITaskButton: Locator;
  readonly noTasksMessage: Locator;

  // Actions
  readonly saveButton: Locator;
  readonly alert: Locator;

  constructor(page: Page) {
    this.page = page;
    this.header = new HeaderComponent(page);
    this.container = page.getByTestId('settings-page');

    this.neo4jCard = page.getByTestId('neo4j-card');
    this.neo4jUri = page.getByTestId('neo4j-uri');
    this.neo4jDatabase = page.getByTestId('neo4j-database');
    this.neo4jUsername = page.getByTestId('neo4j-username');
    this.neo4jPassword = page.getByTestId('neo4j-password');
    this.testConnectionButton = page.getByTestId('neo4j-test-connection');

    this.repositoriesCard = page.getByTestId('repositories-card');
    this.addRepositoryButton = page.getByTestId('add-repository');
    this.noReposMessage = page.getByTestId('no-repos-message');

    this.aiProvidersCard = page.getByTestId('ai-providers-card');
    this.addAIProviderButton = page.getByTestId('add-ai-provider');
    this.noProvidersMessage = page.getByTestId('no-providers-message');

    this.aiTasksCard = page.getByTestId('ai-tasks-card');
    this.addAITaskButton = page.getByTestId('add-ai-task');
    this.noTasksMessage = page.getByTestId('no-tasks-message');

    this.saveButton = page.getByTestId('save-settings');
    this.alert = page.getByTestId('settings-alert');
  }

  async goto() {
    await this.page.goto('/settings');
    await this.container.waitFor();
  }

  async isVisible(): Promise<boolean> {
    return this.container.isVisible();
  }

  // --- Neo4j helpers ---

  async getNeo4jUri(): Promise<string> {
    return this.neo4jUri.inputValue();
  }

  async getNeo4jDatabase(): Promise<string> {
    return this.neo4jDatabase.inputValue();
  }

  async getNeo4jUsername(): Promise<string> {
    return this.neo4jUsername.inputValue();
  }

  async setNeo4jUri(value: string) {
    await this.neo4jUri.fill(value);
  }

  async setNeo4jDatabase(value: string) {
    await this.neo4jDatabase.fill(value);
  }

  async setNeo4jUsername(value: string) {
    await this.neo4jUsername.fill(value);
  }

  async setNeo4jPassword(value: string) {
    await this.neo4jPassword.fill(value);
  }

  async clickTestConnection() {
    await this.testConnectionButton.click();
  }

  // --- Repository helpers ---

  async addRepository() {
    await this.addRepositoryButton.click();
  }

  getRepoBlock(index: number): Locator {
    return this.page.getByTestId(`repo-${index}`);
  }

  getRepoPathInput(index: number): Locator {
    return this.page.getByTestId(`repo-path-${index}`);
  }

  getRemoveRepoButton(index: number): Locator {
    return this.page.getByTestId(`remove-repo-${index}`);
  }

  getAnalyzeRepoButton(index: number): Locator {
    return this.page.getByTestId(`analyze-repo-${index}`);
  }

  async setRepoPath(index: number, path: string) {
    await this.getRepoPathInput(index).fill(path);
  }

  async removeRepository(index: number) {
    await this.getRemoveRepoButton(index).click();
  }

  async getRepoCount(): Promise<number> {
    return this.page.locator('[data-testid^="repo-"]:not([data-testid*="-path"]):not([data-testid*="-remove"]):not([data-testid*="-analyze"])').count();
  }

  // --- Module helpers ---

  getAddModuleButton(repoIndex: number): Locator {
    return this.page.getByTestId(`add-module-${repoIndex}`);
  }

  async addModule(repoIndex: number) {
    await this.getAddModuleButton(repoIndex).click();
  }

  getModuleRow(repoIndex: number, moduleIndex: number): Locator {
    return this.page.getByTestId(`module-${repoIndex}-${moduleIndex}`);
  }

  getModuleNameInput(repoIndex: number, moduleIndex: number): Locator {
    return this.page.getByTestId(`module-name-${repoIndex}-${moduleIndex}`);
  }

  getModuleTypeSelect(repoIndex: number, moduleIndex: number): Locator {
    return this.page.getByTestId(`module-type-${repoIndex}-${moduleIndex}`);
  }

  getModulePathInput(repoIndex: number, moduleIndex: number): Locator {
    return this.page.getByTestId(`module-path-${repoIndex}-${moduleIndex}`);
  }

  getRemoveModuleButton(repoIndex: number, moduleIndex: number): Locator {
    return this.page.getByTestId(`remove-module-${repoIndex}-${moduleIndex}`);
  }

  async setModuleName(repoIndex: number, moduleIndex: number, name: string) {
    await this.getModuleNameInput(repoIndex, moduleIndex).fill(name);
  }

  async setModuleType(repoIndex: number, moduleIndex: number, type: 'java' | 'angular') {
    await this.getModuleTypeSelect(repoIndex, moduleIndex).selectOption(type);
  }

  async setModulePath(repoIndex: number, moduleIndex: number, path: string) {
    await this.getModulePathInput(repoIndex, moduleIndex).fill(path);
  }

  async removeModule(repoIndex: number, moduleIndex: number) {
    await this.getRemoveModuleButton(repoIndex, moduleIndex).click();
  }

  // --- AI Provider helpers ---

  async addAIProvider() {
    await this.addAIProviderButton.click();
  }

  getAIProviderBlock(index: number): Locator {
    return this.page.getByTestId(`ai-provider-${index}`);
  }

  getAIProviderNameInput(index: number): Locator {
    return this.page.getByTestId(`ai-provider-name-${index}`);
  }

  getAIProviderUrlInput(index: number): Locator {
    return this.page.getByTestId(`ai-provider-url-${index}`);
  }

  getAIProviderKeyInput(index: number): Locator {
    return this.page.getByTestId(`ai-provider-key-${index}`);
  }

  getAIProviderModelInput(index: number): Locator {
    return this.page.getByTestId(`ai-provider-model-${index}`);
  }

  getRemoveAIProviderButton(index: number): Locator {
    return this.page.getByTestId(`remove-ai-provider-${index}`);
  }

  async setAIProviderName(index: number, name: string) {
    await this.getAIProviderNameInput(index).fill(name);
  }

  async setAIProviderUrl(index: number, url: string) {
    await this.getAIProviderUrlInput(index).fill(url);
  }

  async setAIProviderKey(index: number, key: string) {
    await this.getAIProviderKeyInput(index).fill(key);
  }

  async setAIProviderModel(index: number, model: string) {
    await this.getAIProviderModelInput(index).fill(model);
  }

  async removeAIProvider(index: number) {
    await this.getRemoveAIProviderButton(index).click();
  }

  // --- AI Task helpers ---

  async addAITask() {
    await this.addAITaskButton.click();
  }

  getAITaskBlock(index: number): Locator {
    return this.page.getByTestId(`ai-task-${index}`);
  }

  getAITaskTypeSelect(index: number): Locator {
    return this.page.getByTestId(`ai-task-type-${index}`);
  }

  getAITaskProviderSelect(index: number): Locator {
    return this.page.getByTestId(`ai-task-provider-${index}`);
  }

  getRemoveAITaskButton(index: number): Locator {
    return this.page.getByTestId(`remove-ai-task-${index}`);
  }

  async setAITaskType(index: number, type: string) {
    await this.getAITaskTypeSelect(index).selectOption(type);
  }

  async setAITaskProvider(index: number, provider: string) {
    await this.getAITaskProviderSelect(index).selectOption(provider);
  }

  async removeAITask(index: number) {
    await this.getRemoveAITaskButton(index).click();
  }

  // --- Actions ---

  async save() {
    await this.saveButton.click();
  }

  async waitForAlert(): Promise<string> {
    await this.alert.waitFor({ timeout: 10000 });
    return (await this.alert.textContent()) ?? '';
  }

  async getAlertText(): Promise<string> {
    return (await this.alert.textContent()) ?? '';
  }

  async isAlertSuccess(): Promise<boolean> {
    return this.alert.evaluate((el) => el.classList.contains('alert-success'));
  }

  async isAlertDanger(): Promise<boolean> {
    return this.alert.evaluate((el) => el.classList.contains('alert-danger'));
  }
}

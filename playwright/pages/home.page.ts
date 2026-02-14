import { type Page, type Locator } from '@playwright/test';
import { HeaderComponent } from './header.page.js';

export class HomePage {
  readonly page: Page;
  readonly header: HeaderComponent;
  readonly container: Locator;
  readonly title: Locator;
  readonly subtitle: Locator;
  readonly openSettingsButton: Locator;

  constructor(page: Page) {
    this.page = page;
    this.header = new HeaderComponent(page);
    this.container = page.getByTestId('home-page');
    this.title = page.getByTestId('home-title');
    this.subtitle = page.getByTestId('home-subtitle');
    this.openSettingsButton = page.getByTestId('home-open-settings');
  }

  async goto() {
    await this.page.goto('/');
    await this.container.waitFor();
  }

  async getTitleText(): Promise<string> {
    return (await this.title.textContent()) ?? '';
  }

  async getSubtitleText(): Promise<string> {
    return (await this.subtitle.textContent()) ?? '';
  }

  async clickOpenSettings() {
    await this.openSettingsButton.click();
  }

  async isVisible(): Promise<boolean> {
    return this.container.isVisible();
  }
}

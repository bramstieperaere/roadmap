import { type Page, type Locator } from '@playwright/test';

export class HeaderComponent {
  readonly page: Page;
  readonly navbar: Locator;
  readonly brand: Locator;
  readonly settingsLink: Locator;
  readonly lockIcon: Locator;

  constructor(page: Page) {
    this.page = page;
    this.navbar = page.getByTestId('header');
    this.brand = page.getByTestId('header-brand');
    this.settingsLink = page.getByTestId('header-settings-link');
    this.lockIcon = page.getByTestId('header-lock-icon');
  }

  async clickBrand() {
    await this.brand.click();
  }

  async navigateToSettings() {
    await this.settingsLink.click();
  }

  async getBrandText(): Promise<string> {
    return (await this.brand.textContent()) ?? '';
  }

  async isVisible(): Promise<boolean> {
    return this.navbar.isVisible();
  }
}

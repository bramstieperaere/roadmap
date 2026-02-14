import { type Page, type Locator } from '@playwright/test';

export class UnlockPopupPage {
  readonly page: Page;
  readonly overlay: Locator;
  readonly card: Locator;
  readonly passwordInput: Locator;
  readonly submitButton: Locator;
  readonly firstTimeWarning: Locator;
  readonly error: Locator;

  constructor(page: Page) {
    this.page = page;
    this.overlay = page.getByTestId('unlock-popup');
    this.card = page.getByTestId('unlock-card');
    this.passwordInput = page.getByTestId('unlock-password-input');
    this.submitButton = page.getByTestId('unlock-submit');
    this.firstTimeWarning = page.getByTestId('unlock-first-time-warning');
    this.error = page.getByTestId('unlock-error');
  }

  async isVisible(): Promise<boolean> {
    return this.overlay.isVisible();
  }

  async unlock(password: string) {
    await this.passwordInput.fill(password);
    await this.submitButton.click();
    await this.overlay.waitFor({ state: 'hidden', timeout: 10000 });
  }

  async attemptUnlock(password: string) {
    await this.passwordInput.fill(password);
    await this.submitButton.click();
  }

  async getErrorText(): Promise<string> {
    await this.error.waitFor({ timeout: 5000 });
    return (await this.error.textContent()) ?? '';
  }
}

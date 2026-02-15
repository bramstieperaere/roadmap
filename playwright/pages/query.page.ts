import { type Page, type Locator } from '@playwright/test';

export class QueryPage {
  readonly page: Page;
  readonly queryInput: Locator;
  readonly submitButton: Locator;
  readonly clearButton: Locator;
  readonly cypherBar: Locator;
  readonly errorAlert: Locator;
  readonly graphContainer: Locator;
  readonly contextMenu: Locator;

  constructor(page: Page) {
    this.page = page;
    this.queryInput = page.getByTestId('query-input');
    this.submitButton = page.getByTestId('query-submit');
    this.clearButton = page.getByTestId('query-clear');
    this.cypherBar = page.getByTestId('cypher-bar');
    this.errorAlert = page.getByTestId('query-error');
    this.graphContainer = page.getByTestId('graph-container');
    this.contextMenu = page.getByTestId('context-menu');
  }

  async goto() {
    await this.page.goto('/query');
  }
}

import { type Page, type Locator } from '@playwright/test';
import { HeaderComponent } from './header.page.js';

export class JobsListPage {
  readonly page: Page;
  readonly header: HeaderComponent;
  readonly container: Locator;
  readonly noJobsMessage: Locator;
  readonly tableCard: Locator;

  constructor(page: Page) {
    this.page = page;
    this.header = new HeaderComponent(page);
    this.container = page.getByTestId('jobs-list-page');
    this.noJobsMessage = page.getByTestId('no-jobs-message');
    this.tableCard = page.getByTestId('jobs-table-card');
  }

  async goto() {
    await this.page.goto('/jobs');
    await this.container.waitFor();
  }

  async isVisible(): Promise<boolean> {
    return this.container.isVisible();
  }

  getJobRow(jobId: string): Locator {
    return this.page.getByTestId(`job-row-${jobId}`);
  }

  getJobStatus(jobId: string): Locator {
    return this.page.getByTestId(`job-status-${jobId}`);
  }

  getViewJobButton(jobId: string): Locator {
    return this.page.getByTestId(`view-job-${jobId}`);
  }

  async clickJob(jobId: string) {
    await this.getJobRow(jobId).click();
  }
}

export class JobDetailPage {
  readonly page: Page;
  readonly header: HeaderComponent;
  readonly container: Locator;
  readonly backLink: Locator;
  readonly infoCard: Locator;
  readonly logCard: Locator;
  readonly status: Locator;
  readonly moduleName: Locator;
  readonly repoPath: Locator;
  readonly summary: Locator;
  readonly error: Locator;
  readonly logEntries: Locator;
  readonly noLogMessage: Locator;

  constructor(page: Page) {
    this.page = page;
    this.header = new HeaderComponent(page);
    this.container = page.getByTestId('job-detail-page');
    this.backLink = page.getByTestId('back-to-jobs');
    this.infoCard = page.getByTestId('job-info-card');
    this.logCard = page.getByTestId('job-log-card');
    this.status = page.getByTestId('job-detail-status');
    this.moduleName = page.getByTestId('job-module-name');
    this.repoPath = page.getByTestId('job-repo-path');
    this.summary = page.getByTestId('job-summary');
    this.error = page.getByTestId('job-error');
    this.logEntries = page.getByTestId('job-log-entries');
    this.noLogMessage = page.getByTestId('no-log-message');
  }

  async goto(jobId: string) {
    await this.page.goto(`/jobs/${jobId}`);
    await this.container.waitFor();
  }

  async isVisible(): Promise<boolean> {
    return this.container.isVisible();
  }

  async clickBack() {
    await this.backLink.click();
  }

  getLogEntry(index: number): Locator {
    return this.page.getByTestId(`log-entry-${index}`);
  }
}

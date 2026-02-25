import { Component, inject, effect, signal } from '@angular/core';
import { Router } from '@angular/router';
import { DatePipe } from '@angular/common';
import { JiraService, SprintBoard } from '../services/jira';
import { JiraStateService } from './jira-state';

@Component({
  selector: 'app-jira-sprint',
  imports: [DatePipe],
  templateUrl: './jira-sprint.html',
  styleUrl: './jira-sprint.scss',
})
export class JiraSprintComponent {
  private jiraService = inject(JiraService);
  private router = inject(Router);
  private state = inject(JiraStateService);

  sprint = signal<SprintBoard | null>(null);
  loading = signal(false);
  error = signal('');
  refreshingIssues = signal(false);
  refreshMessage = signal('');

  constructor() {
    effect(() => {
      const key = this.state.selectedProjectKey();
      if (key) this.load(key, false);
    }, { allowSignalWrites: true });
  }

  refresh() {
    const key = this.state.selectedProjectKey();
    if (key) this.load(key, true);
  }

  private load(projectKey: string, forceRefresh: boolean) {
    this.loading.set(true);
    this.error.set('');
    this.sprint.set(null);
    this.jiraService.getSprint(projectKey, forceRefresh).subscribe({
      next: (data) => {
        this.sprint.set(data);
        this.loading.set(false);
      },
      error: (err) => {
        this.loading.set(false);
        this.error.set(err.error?.detail || 'Failed to load sprint');
      },
    });
  }

  downloadAllIssues() {
    const key = this.state.selectedProjectKey();
    const issues = this.sprint()?.issues;
    if (!key || !issues?.length) return;
    this.refreshingIssues.set(true);
    this.refreshMessage.set(`Downloading ${issues.length} issue(s)...`);
    this.jiraService.refreshIssues(key, issues.map(i => i.key)).subscribe({
      next: (result) => {
        this.refreshingIssues.set(false);
        const msg = `Downloaded ${result.issues_refreshed}/${result.issues_total} issues` +
          (result.errors.length ? ` (${result.errors.length} errors)` : '');
        this.refreshMessage.set(msg);
        setTimeout(() => this.refreshMessage.set(''), 5000);
      },
      error: (err) => {
        this.refreshingIssues.set(false);
        this.refreshMessage.set(err.error?.detail || 'Download failed');
        setTimeout(() => this.refreshMessage.set(''), 5000);
      },
    });
  }

  openIssue(key: string) {
    this.router.navigate(['/jira/issue', key]);
  }

  getStatusClass(status: string): string {
    const s = status.toLowerCase();
    if (s === 'done' || s === 'closed' || s === 'resolved') return 'badge-done';
    if (s === 'in progress' || s === 'in review') return 'badge-progress';
    return 'badge-todo';
  }

  getTypeIcon(type: string): string {
    const t = type.toLowerCase();
    if (t === 'bug') return 'bi-bug';
    if (t === 'epic') return 'bi-lightning';
    if (t === 'sub-task' || t === 'subtask') return 'bi-card-list';
    return 'bi-bookmark';
  }
}

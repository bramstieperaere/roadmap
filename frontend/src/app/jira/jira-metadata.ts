import { Component, inject, effect, signal } from '@angular/core';
import { Router } from '@angular/router';
import { DatePipe } from '@angular/common';
import { JiraService, JiraProjectMetadata } from '../services/jira';
import { JiraStateService } from './jira-state';

@Component({
  selector: 'app-jira-metadata',
  imports: [DatePipe],
  templateUrl: './jira-metadata.html',
  styleUrl: './jira-metadata.scss',
})
export class JiraMetadataComponent {
  private jiraService = inject(JiraService);
  private router = inject(Router);
  private state = inject(JiraStateService);

  metadata = signal<JiraProjectMetadata | null>(null);
  loading = signal(false);
  error = signal('');
  activeSection = signal('components');

  sections = [
    { id: 'components', label: 'Components', icon: 'bi-puzzle' },
    { id: 'versions', label: 'Versions', icon: 'bi-tag' },
    { id: 'issue_types', label: 'Issue Types', icon: 'bi-bookmark' },
    { id: 'priorities', label: 'Priorities', icon: 'bi-flag' },
    { id: 'epics', label: 'Epics', icon: 'bi-lightning' },
    { id: 'labels', label: 'Labels', icon: 'bi-tags' },
  ];

  constructor() {
    effect(() => {
      const key = this.state.selectedProjectKey();
      if (key) this.load(key, false);
    }, { allowSignalWrites: true });
  }

  refresh() {
    const key = this.state.selectedProjectKey();
    if (!key) return;
    this.load(key, true);
  }

  private load(projectKey: string, forceRefresh: boolean) {
    this.loading.set(true);
    this.error.set('');
    this.metadata.set(null);
    this.jiraService.getProjectMetadata(projectKey, forceRefresh).subscribe({
      next: (data) => {
        this.metadata.set(data);
        this.loading.set(false);
      },
      error: (err) => {
        this.loading.set(false);
        this.error.set(err.error?.detail || 'Failed to load metadata');
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

  getCategoryClass(category: string): string {
    const c = category.toLowerCase();
    if (c === 'done') return 'badge-done';
    if (c === 'in progress') return 'badge-progress';
    return 'badge-todo';
  }
}

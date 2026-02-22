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

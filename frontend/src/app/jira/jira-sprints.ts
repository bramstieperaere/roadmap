import { Component, inject, effect, signal } from '@angular/core';
import { Router } from '@angular/router';
import { DatePipe } from '@angular/common';
import { JiraService, SprintsList, SprintSummary } from '../services/jira';
import { JiraStateService } from './jira-state';

@Component({
  selector: 'app-jira-sprints',
  imports: [DatePipe],
  templateUrl: './jira-sprints.html',
  styleUrl: './jira-sprints.scss',
})
export class JiraSprintsComponent {
  private jiraService = inject(JiraService);
  private router = inject(Router);
  private state = inject(JiraStateService);

  data = signal<SprintsList | null>(null);
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
    this.data.set(null);
    this.jiraService.listSprints(projectKey, forceRefresh).subscribe({
      next: (d) => {
        this.data.set(d);
        this.loading.set(false);
      },
      error: (err) => {
        this.loading.set(false);
        this.error.set(err.error?.detail || 'Failed to load sprints');
      },
    });
  }

  openSprint(sprint: SprintSummary) {
    this.router.navigate(['/jira/sprints', sprint.id]);
  }

  getStateClass(state: string): string {
    if (state === 'active') return 'bg-primary';
    if (state === 'closed') return 'bg-secondary';
    return 'bg-light text-dark border';
  }
}

import { Component, inject, effect, signal } from '@angular/core';
import { Router } from '@angular/router';
import { DatePipe } from '@angular/common';
import { JiraService, SprintsList, SprintSummary } from '../services/jira';
import { JiraStateService } from './jira-state';
import { BoardPickerComponent } from './board-picker';

@Component({
  selector: 'app-jira-sprints',
  imports: [DatePipe, BoardPickerComponent],
  templateUrl: './jira-sprints.html',
  styleUrl: './jira-sprints.scss',
})
export class JiraSprintsComponent {
  private jiraService = inject(JiraService);
  private router = inject(Router);
  state = inject(JiraStateService);

  data = signal<SprintsList | null>(null);
  loading = signal(false);
  error = signal('');
  noBoard = signal(false);

  constructor() {
    effect(() => {
      const key = this.state.selectedProjectKey();
      const boardId = this.state.selectedBoardId();
      if (key && boardId) this.load(key, false, boardId);
      else if (key && !this.state.loadingBoards()) this.noBoard.set(true);
    }, { allowSignalWrites: true });
  }

  refresh() {
    const key = this.state.selectedProjectKey();
    const boardId = this.state.selectedBoardId();
    if (key && boardId) this.load(key, true, boardId);
  }

  private load(projectKey: string, forceRefresh: boolean, boardId: number) {
    this.loading.set(true);
    this.error.set('');
    this.noBoard.set(false);
    this.data.set(null);
    this.jiraService.listSprints(projectKey, forceRefresh, boardId).subscribe({
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

  onBoardSelected() {
    const key = this.state.selectedProjectKey();
    const boardId = this.state.selectedBoardId();
    if (key && boardId) this.load(key, false, boardId);
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

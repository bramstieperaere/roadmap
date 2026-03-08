import { Component, inject, signal, output, OnInit, input } from '@angular/core';
import { JiraService, JiraBoardOption } from '../services/jira';

@Component({
  selector: 'app-board-picker',
  standalone: true,
  template: `
    <div class="card border-warning m-3">
      <div class="card-body">
        <h6 class="card-title mb-3">
          <i class="bi bi-kanban me-2"></i>No board configured for {{ projectKey() }}
        </h6>
        <p class="text-muted small mb-3">Select a board to enable sprint and backlog views.</p>
        @if (loadingBoards()) {
          <div class="d-flex align-items-center gap-2 text-muted">
            <span class="spinner-border spinner-border-sm"></span>
            Loading boards...
          </div>
        } @else if (boards().length === 0) {
          <div class="text-muted">No boards found for this project in Jira.</div>
        } @else {
          <div class="list-group">
            @for (board of boards(); track board.id) {
              <button class="list-group-item list-group-item-action d-flex justify-content-between align-items-center"
                      [disabled]="saving()"
                      (click)="selectBoard(board)"
                      [attr.data-testid]="'board-' + board.id">
                <span>
                  <i class="bi bi-kanban me-2 text-primary"></i>{{ board.name }}
                </span>
                <span class="text-muted small">#{{ board.id }}</span>
              </button>
            }
          </div>
        }
        @if (error()) {
          <div class="alert alert-danger mt-3 mb-0 py-2">{{ error() }}</div>
        }
      </div>
    </div>
  `,
})
export class BoardPickerComponent implements OnInit {
  private jiraService = inject(JiraService);

  projectKey = input.required<string>();
  boardSelected = output<number>();

  boards = signal<JiraBoardOption[]>([]);
  loadingBoards = signal(true);
  saving = signal(false);
  error = signal('');

  ngOnInit() {
    this.jiraService.getBoards(this.projectKey()).subscribe({
      next: (boards) => {
        this.boards.set(boards);
        this.loadingBoards.set(false);
      },
      error: () => {
        this.loadingBoards.set(false);
        this.error.set('Failed to load boards. Check Atlassian connection.');
      },
    });
  }

  selectBoard(board: JiraBoardOption) {
    this.saving.set(true);
    this.error.set('');
    this.jiraService.setBoard(this.projectKey(), board.id).subscribe({
      next: () => {
        this.saving.set(false);
        this.boardSelected.emit(board.id);
      },
      error: (err) => {
        this.saving.set(false);
        this.error.set(err.error?.detail || 'Failed to save board selection');
      },
    });
  }
}

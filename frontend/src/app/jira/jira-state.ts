import { Injectable, inject, signal, computed } from '@angular/core';
import { JiraProject, JiraBoardOption, JiraService } from '../services/jira';

@Injectable({ providedIn: 'root' })
export class JiraStateService {
  private jiraService = inject(JiraService);

  projects = signal<JiraProject[]>([]);
  loadingProjects = signal(true);
  selectedProjectKey = signal<string>('');

  boards = signal<JiraBoardOption[]>([]);
  loadingBoards = signal(false);
  selectedBoardId = signal<number | null>(null);

  defaultBoardId = computed(() => {
    const key = this.selectedProjectKey();
    const project = this.projects().find(p => p.key === key);
    return project?.board_id ?? null;
  });

  isDefaultBoard = computed(() => {
    const sel = this.selectedBoardId();
    const def = this.defaultBoardId();
    return sel !== null && def !== null && sel === def;
  });

  loadBoards(projectKey: string) {
    this.loadingBoards.set(true);
    this.boards.set([]);
    this.jiraService.getBoards(projectKey).subscribe({
      next: (boards) => {
        this.boards.set(boards);
        this.loadingBoards.set(false);
        // Default to config board_id, or first board, or null
        const project = this.projects().find(p => p.key === projectKey);
        const configBoardId = project?.board_id;
        if (configBoardId && boards.some(b => b.id === configBoardId)) {
          this.selectedBoardId.set(configBoardId);
        } else if (boards.length > 0) {
          this.selectedBoardId.set(boards[0].id);
        } else {
          this.selectedBoardId.set(null);
        }
      },
      error: () => {
        this.loadingBoards.set(false);
        this.selectedBoardId.set(null);
      },
    });
  }

  setDefaultBoard(boardId: number) {
    const key = this.selectedProjectKey();
    if (!key) return;
    this.jiraService.setBoard(key, boardId).subscribe({
      next: () => {
        // Update local project data to reflect new default
        this.projects.update(projects =>
          projects.map(p => p.key === key ? { ...p, board_id: boardId } : p)
        );
      },
    });
  }
}

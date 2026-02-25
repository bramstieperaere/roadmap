import { Component, inject, OnInit, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { DatePipe } from '@angular/common';
import { JiraService, JiraProject, SprintBoard, JiraIssueSummary } from '../services/jira';

@Component({
  selector: 'app-jira-board',
  imports: [DatePipe, RouterLink],
  templateUrl: './jira-board.html',
  styleUrl: './jira-board.scss',
})
export class JiraBoardComponent implements OnInit {
  private jiraService = inject(JiraService);
  private router = inject(Router);

  projects = signal<JiraProject[]>([]);
  selectedProject = signal<JiraProject | null>(null);
  sprint = signal<SprintBoard | null>(null);
  loading = signal(false);
  loadingProjects = signal(true);
  error = signal('');

  ngOnInit() {
    this.jiraService.getProjects().subscribe({
      next: (projects) => {
        this.projects.set(projects);
        this.loadingProjects.set(false);
        if (projects.length > 0) {
          this.selectProject(projects[0]);
        }
      },
      error: () => {
        this.loadingProjects.set(false);
        this.error.set('Failed to load projects. Check Atlassian settings.');
      },
    });
  }

  selectProject(project: JiraProject) {
    this.selectedProject.set(project);
    this.loadSprint(project.key);
  }

  refreshSprint() {
    const project = this.selectedProject();
    if (!project) return;
    this.loading.set(true);
    this.error.set('');
    this.sprint.set(null);
    this.jiraService.getSprint(project.key, true).subscribe({
      next: (data) => {
        this.sprint.set(data);
        this.loading.set(false);
      },
      error: (err) => {
        this.loading.set(false);
        this.error.set(err.error?.detail || 'Failed to refresh sprint');
      },
    });
  }

  loadSprint(projectKey: string) {
    this.loading.set(true);
    this.error.set('');
    this.sprint.set(null);
    this.jiraService.getSprint(projectKey).subscribe({
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

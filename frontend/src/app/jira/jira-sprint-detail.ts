import { Component, inject, OnInit, signal } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { DatePipe } from '@angular/common';
import { JiraService, SprintDetail } from '../services/jira';
import { JiraStateService } from './jira-state';

@Component({
  selector: 'app-jira-sprint-detail',
  imports: [DatePipe],
  templateUrl: './jira-sprint-detail.html',
  styleUrl: './jira-sprint-detail.scss',
})
export class JiraSprintDetailComponent implements OnInit {
  private jiraService = inject(JiraService);
  private router = inject(Router);
  private route = inject(ActivatedRoute);
  private state = inject(JiraStateService);

  data = signal<SprintDetail | null>(null);
  loading = signal(false);
  error = signal('');
  refreshingIssues = signal(false);
  refreshMessage = signal('');

  ngOnInit() {
    this.load(false);
  }

  refresh() {
    this.load(true);
  }

  private load(forceRefresh: boolean) {
    const sprintId = Number(this.route.snapshot.paramMap.get('id'));
    const projectKey = this.state.selectedProjectKey();
    if (!projectKey) {
      this.error.set('No project selected.');
      return;
    }
    this.loading.set(true);
    this.error.set('');
    this.data.set(null);
    this.jiraService.getSprintById(projectKey, sprintId, forceRefresh).subscribe({
      next: (d) => {
        this.data.set(d);
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
    const issues = this.data()?.issues;
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

  goBack() {
    this.router.navigate(['/jira/sprints']);
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

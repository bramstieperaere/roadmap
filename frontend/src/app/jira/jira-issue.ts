import { Component, inject, OnInit, signal } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { DatePipe } from '@angular/common';
import { JiraService, JiraIssue } from '../services/jira';

@Component({
  selector: 'app-jira-issue',
  imports: [DatePipe],
  templateUrl: './jira-issue.html',
  styleUrl: './jira-issue.scss',
})
export class JiraIssueComponent implements OnInit {
  private jiraService = inject(JiraService);
  private route = inject(ActivatedRoute);
  private router = inject(Router);

  issue = signal<JiraIssue | null>(null);
  loading = signal(true);
  error = signal('');

  ngOnInit() {
    this.fetchIssue(false);
  }

  refreshIssue() {
    this.fetchIssue(true);
  }

  private fetchIssue(refresh: boolean) {
    const key = this.route.snapshot.paramMap.get('key')!;
    this.loading.set(true);
    this.error.set('');
    this.jiraService.getIssue(key, refresh).subscribe({
      next: (data) => {
        data.branches = data.branches || [];
        data.commits = data.commits || [];
        data.pull_requests = data.pull_requests || [];
        this.issue.set(data);
        this.loading.set(false);
      },
      error: (err) => {
        this.loading.set(false);
        this.error.set(err.error?.detail || 'Failed to load issue');
      },
    });
  }

  goBack() {
    this.router.navigate(['/jira/sprint']);
  }

  openIssue(key: string) {
    this.router.navigate(['/jira/issue', key]);
  }

  activeSection = signal('details');

  scrollTo(section: string) {
    this.activeSection.set(section);
    document.getElementById('section-' + section)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  getStatusClass(status: string): string {
    const s = status.toLowerCase();
    if (s === 'done' || s === 'closed' || s === 'resolved') return 'badge-done';
    if (s === 'in progress' || s === 'in review') return 'badge-progress';
    return 'badge-todo';
  }
}

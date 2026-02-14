import { Component, inject, OnInit, OnDestroy, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { DatePipe } from '@angular/common';
import { JobsService, JobSummary } from '../services/jobs';

@Component({
  selector: 'app-jobs-list',
  imports: [RouterLink, DatePipe],
  templateUrl: './jobs-list.html',
  styleUrl: './jobs-list.scss',
})
export class JobsListComponent implements OnInit, OnDestroy {
  private jobsService = inject(JobsService);
  private router = inject(Router);

  jobs = signal<JobSummary[]>([]);
  loading = signal(true);
  private pollInterval: ReturnType<typeof setInterval> | null = null;

  ngOnInit() {
    this.loadJobs();
    this.pollInterval = setInterval(() => this.loadJobs(), 2000);
  }

  ngOnDestroy() {
    if (this.pollInterval) {
      clearInterval(this.pollInterval);
    }
  }

  loadJobs() {
    this.jobsService.listJobs().subscribe({
      next: (result) => {
        this.jobs.set(result.jobs);
        this.loading.set(false);
        const hasActive = result.jobs.some(
          j => j.status === 'running' || j.status === 'pending');
        if (!hasActive && this.pollInterval) {
          clearInterval(this.pollInterval);
          this.pollInterval = null;
        }
      },
      error: () => this.loading.set(false),
    });
  }

  viewJob(jobId: string) {
    this.router.navigate(['/jobs', jobId]);
  }

  getStatusBadgeClass(status: string): string {
    switch (status) {
      case 'completed': return 'bg-success';
      case 'failed': return 'bg-danger';
      case 'running': return 'bg-primary';
      default: return 'bg-secondary';
    }
  }
}

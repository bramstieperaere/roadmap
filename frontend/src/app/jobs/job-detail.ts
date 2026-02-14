import { Component, inject, OnInit, OnDestroy, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { DatePipe } from '@angular/common';
import { JobsService, JobDetail } from '../services/jobs';

@Component({
  selector: 'app-job-detail',
  imports: [RouterLink, DatePipe],
  templateUrl: './job-detail.html',
  styleUrl: './job-detail.scss',
})
export class JobDetailComponent implements OnInit, OnDestroy {
  private route = inject(ActivatedRoute);
  private jobsService = inject(JobsService);

  job = signal<JobDetail | null>(null);
  loading = signal(true);
  private pollInterval: ReturnType<typeof setInterval> | null = null;

  ngOnInit() {
    const jobId = this.route.snapshot.paramMap.get('id')!;
    this.loadJob(jobId);
    this.pollInterval = setInterval(() => this.loadJob(jobId), 2000);
  }

  ngOnDestroy() {
    if (this.pollInterval) {
      clearInterval(this.pollInterval);
    }
  }

  loadJob(jobId: string) {
    this.jobsService.getJob(jobId).subscribe({
      next: (result) => {
        this.job.set(result.job);
        this.loading.set(false);
        if (result.job.status === 'completed' || result.job.status === 'failed') {
          if (this.pollInterval) {
            clearInterval(this.pollInterval);
            this.pollInterval = null;
          }
        }
      },
      error: () => this.loading.set(false),
    });
  }

  getStatusBadgeClass(status: string): string {
    switch (status) {
      case 'completed': return 'bg-success';
      case 'failed': return 'bg-danger';
      case 'running': return 'bg-primary';
      default: return 'bg-secondary';
    }
  }

  getLogLevelClass(level: string): string {
    switch (level) {
      case 'error': return 'text-danger';
      case 'warn': return 'text-warning';
      default: return '';
    }
  }
}

import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

export interface JobLogEntry {
  timestamp: string;
  level: string;
  message: string;
}

export interface JobSummary {
  id: string;
  repo_path: string;
  module_name: string;
  module_type: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  created_at: string;
  completed_at: string | null;
  summary: string | null;
  error: string | null;
}

export interface JobDetail {
  id: string;
  repo_path: string;
  repo_index: number;
  module_name: string;
  module_type: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  log: JobLogEntry[];
  summary: string | null;
  error: string | null;
}

export interface StartJobResponse {
  job_id: string;
  message: string;
}

@Injectable({ providedIn: 'root' })
export class JobsService {
  private http = inject(HttpClient);

  startJob(repoIndex: number, moduleIndex: number): Observable<StartJobResponse> {
    return this.http.post<StartJobResponse>('/api/jobs/start', {
      repo_index: repoIndex,
      module_index: moduleIndex,
    });
  }

  listJobs(): Observable<{ jobs: JobSummary[] }> {
    return this.http.get<{ jobs: JobSummary[] }>('/api/jobs');
  }

  getJob(jobId: string): Observable<{ job: JobDetail }> {
    return this.http.get<{ job: JobDetail }>(`/api/jobs/${jobId}`);
  }
}

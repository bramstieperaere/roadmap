import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { StartJobResponse } from './jobs';

export interface Repository {
  name: string;
  path: string;
}

export interface JiraProjectInfo {
  issue_keys: string[];
  reference_count: number;
  unique_issues: number;
}

export interface RepoResult {
  repo_name: string;
  repo_path: string;
  total_commits: number;
  total_issue_keys: number;
  projects: Record<string, JiraProjectInfo>;
}

export interface MiningResults {
  repos: Record<string, RepoResult>;
  summary: {
    total_repos: number;
    total_commits: number;
    total_issue_keys: number;
  };
}

@Injectable({ providedIn: 'root' })
export class GitMiningService {
  private http = inject(HttpClient);

  getRepositories(): Observable<Repository[]> {
    return this.http.get<Repository[]>('/api/contexts/meta/repositories');
  }

  start(action: string, repoNames: string[]): Observable<StartJobResponse> {
    return this.http.post<StartJobResponse>('/api/git-mining/start', {
      action,
      repo_names: repoNames,
    });
  }

  getResults(): Observable<MiningResults> {
    return this.http.get<MiningResults>('/api/git-mining/results');
  }
}

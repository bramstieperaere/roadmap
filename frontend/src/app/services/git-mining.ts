import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { StartJobResponse } from './jobs';

export interface Repository {
  name: string;
  path: string;
}

export interface BranchInfo {
  name: string;
  latest_date: string | null;
  tip_hash: string | null;
}

export interface CommitInfo {
  hash: string;
  message: string;
  author_name: string;
  author_email: string;
  date: string;
  issue_keys: string[];
  files_count: number;
  db_changes?: string | null;
  jpa_entities?: string | null;
  spring_endpoints?: string | null;
  spring_messaging?: string | null;
  spring_datasource?: string | null;
  documented_files?: string[] | null;
  uncovered_files?: string[] | null;
}

export interface ProcessorInfo {
  name: string;
  label: string;
  description: string;
  node_property: string;
  status: 'matured' | 'incubating';
}

export interface IncubatingProcessorConfig {
  name: string;
  label: string;
  description: string;
  instructions: string;
  file_patterns: string[];
  instance_count: number;
}

export interface ProcessorSuggestion {
  name: string;
  label: string;
  description: string;
  instructions: string;
  file_patterns: string[];
  covers_groups?: string[];
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

  start(action: string, repoNames: string[], branches?: string[], processingConfig?: string): Observable<StartJobResponse> {
    const body: Record<string, unknown> = { action, repo_names: repoNames };
    if (branches && branches.length > 0) body['branches'] = branches;
    if (processingConfig) body['processing_config'] = processingConfig;
    return this.http.post<StartJobResponse>('/api/git-mining/start', body);
  }

  getProcessingConfigs(): Observable<{ name: string; repo_name: string; branch: string; processors: string[] }[]> {
    return this.http.get<{ name: string; repo_name: string; branch: string; processors: string[] }[]>('/api/git-mining/processing-configs');
  }

  getProcessors(): Observable<ProcessorInfo[]> {
    return this.http.get<ProcessorInfo[]>('/api/git-mining/processors');
  }

  getBranches(repoName: string): Observable<string[]> {
    return this.http.get<string[]>(`/api/git-mining/branches/${encodeURIComponent(repoName)}`);
  }

  getMergeSourceCommits(commitHash: string, repoName = '', branch = ''): Observable<CommitInfo[]> {
    const p = new URLSearchParams();
    if (repoName) p.set('repo_name', repoName);
    if (branch) p.set('branch', branch);
    const qs = p.toString() ? `?${p.toString()}` : '';
    return this.http.get<CommitInfo[]>(`/api/git-mining/commits/${encodeURIComponent(commitHash)}/merge-source${qs}`);
  }

  getNeo4jBranches(repoName: string): Observable<BranchInfo[]> {
    return this.http.get<BranchInfo[]>(`/api/git-mining/repos/${encodeURIComponent(repoName)}/branches`);
  }

  getCommits(repoName: string, branchName: string): Observable<CommitInfo[]> {
    return this.http.get<CommitInfo[]>(
      `/api/git-mining/repos/${encodeURIComponent(repoName)}/branches/${encodeURIComponent(branchName)}/commits`);
  }

  getMergeRollup(commitHash: string, repoName: string): Observable<Record<string, any>> {
    return this.http.get<Record<string, any>>(
      `/api/git-mining/commits/${encodeURIComponent(commitHash)}/rollup`,
      { params: { repo_name: repoName } });
  }

  getFileAtCommit(repoName: string, commitHash: string, filePath: string) {
    return this.http.get<{ path: string; content: string; commit_hash: string; added_lines?: number[]; removed_lines?: number[] }>(
      '/api/git-mining/file-at-commit', {
        params: { repo_name: repoName, commit_hash: commitHash, file_path: filePath },
      });
  }

  getResults(): Observable<MiningResults> {
    return this.http.get<MiningResults>('/api/git-mining/results');
  }

  getProcessorSuggestions(jobId: string): Observable<Record<string, ProcessorSuggestion[]>> {
    return this.http.get<Record<string, ProcessorSuggestion[]>>(`/api/git-mining/jobs/${encodeURIComponent(jobId)}/processor-suggestions`);
  }

  createIncubatingProcessor(body: Partial<IncubatingProcessorConfig>): Observable<{ status: string; name: string }> {
    return this.http.post<{ status: string; name: string }>('/api/git-mining/incubating-processors', body);
  }

  updateIncubatingProcessor(name: string, body: Partial<IncubatingProcessorConfig>): Observable<{ status: string; name: string }> {
    return this.http.put<{ status: string; name: string }>(`/api/git-mining/incubating-processors/${encodeURIComponent(name)}`, body);
  }

  deleteIncubatingProcessor(name: string): Observable<{ status: string; name: string }> {
    return this.http.delete<{ status: string; name: string }>(`/api/git-mining/incubating-processors/${encodeURIComponent(name)}`);
  }
}

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

export interface CommitTag {
  name: string;
  label: string;
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
  documented_files?: string[] | null;
}

export interface ProcessorInfo {
  name: string;
  label: string;
  description: string;
  node_property: string;
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

  start(action: string, repoNames: string[], branches?: string[]): Observable<StartJobResponse> {
    const body: Record<string, unknown> = { action, repo_names: repoNames };
    if (branches && branches.length > 0) body['branches'] = branches;
    return this.http.post<StartJobResponse>('/api/git-mining/start', body);
  }

  getProcessors(): Observable<ProcessorInfo[]> {
    return this.http.get<ProcessorInfo[]>('/api/git-mining/processors');
  }

  getBranches(repoName: string): Observable<string[]> {
    return this.http.get<string[]>(`/api/git-mining/branches/${encodeURIComponent(repoName)}`);
  }

  classifyCommits(hashes: string[]): Observable<StartJobResponse> {
    return this.http.post<StartJobResponse>('/api/git-mining/classify-commits', { commit_hashes: hashes });
  }

  getCommitTags(hash: string): Observable<CommitTag[]> {
    return this.http.get<CommitTag[]>(`/api/git-mining/commits/${encodeURIComponent(hash)}/tags`);
  }

  getMergeSourceCommits(commitHash: string, branch = ''): Observable<CommitInfo[]> {
    const params = branch ? `?branch=${encodeURIComponent(branch)}` : '';
    return this.http.get<CommitInfo[]>(`/api/git-mining/commits/${encodeURIComponent(commitHash)}/merge-source${params}`);
  }

  getNeo4jBranches(repoName: string): Observable<BranchInfo[]> {
    return this.http.get<BranchInfo[]>(`/api/git-mining/repos/${encodeURIComponent(repoName)}/branches`);
  }

  getCommits(repoName: string, branchName: string): Observable<CommitInfo[]> {
    return this.http.get<CommitInfo[]>(
      `/api/git-mining/repos/${encodeURIComponent(repoName)}/branches/${encodeURIComponent(branchName)}/commits`);
  }

  getResults(): Observable<MiningResults> {
    return this.http.get<MiningResults>('/api/git-mining/results');
  }
}

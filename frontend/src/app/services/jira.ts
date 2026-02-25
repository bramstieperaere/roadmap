import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

export interface JiraProject {
  key: string;
  name: string;
  board_id: number | null;
}

export interface JiraIssueSummary {
  key: string;
  summary: string;
  status: string;
  issuetype: string;
  priority: string;
  assignee: string;
}

export interface SprintInfo {
  id: number;
  name: string;
  state: string;
}

export interface SprintBoard {
  sprint: SprintInfo;
  issues: JiraIssueSummary[];
  _cached_at: string;
  from_cache: boolean;
}

export interface JiraIssueLink {
  type: string;
  direction: string;
  key: string;
  summary: string;
}

export interface JiraSubtask {
  key: string;
  summary: string;
  status: string;
}

export interface JiraComment {
  author: string;
  created: string;
  body: string;
}

export interface JiraBranch {
  name: string;
  url: string;
  repository: string;
}

export interface JiraCommit {
  id: string;
  message: string;
  author: string;
  url: string;
  repository: string;
  timestamp: string;
}

export interface JiraPullRequest {
  id: string;
  name: string;
  status: string;
  url: string;
  source_branch: string;
  destination_branch: string;
  author: string;
}

export interface JiraIssue {
  key: string;
  summary: string;
  status: string;
  issuetype: string;
  priority: string;
  assignee: string;
  reporter: string;
  created: string;
  updated: string;
  description: string;
  labels: string[];
  components: string[];
  fix_versions: string[];
  subtasks: JiraSubtask[];
  issuelinks: JiraIssueLink[];
  comment_count: number;
  comments: JiraComment[];
  branches: JiraBranch[];
  commits: JiraCommit[];
  pull_requests: JiraPullRequest[];
  _cached_at: string;
  from_cache: boolean;
}

export interface JiraComponent {
  id: string;
  name: string;
  description: string;
  lead: string;
}

export interface JiraVersion {
  id: string;
  name: string;
  released: boolean;
  release_date: string;
  archived: boolean;
}

export interface JiraStatusInfo {
  id: string;
  name: string;
  category: string;
}

export interface JiraIssueTypeWithStatuses {
  id: string;
  name: string;
  statuses: JiraStatusInfo[];
}

export interface JiraPriority {
  id: string;
  name: string;
  icon_url: string;
}

export interface JiraEpicSummary {
  key: string;
  summary: string;
  status: string;
}

export interface JiraProjectMetadata {
  project_key: string;
  components: JiraComponent[];
  versions: JiraVersion[];
  issue_types: JiraIssueTypeWithStatuses[];
  priorities: JiraPriority[];
  epics: JiraEpicSummary[];
  labels: string[];
  _cached_at: string;
  from_cache: boolean;
}

export interface SprintSummary {
  id: number;
  name: string;
  state: string;
  start_date: string;
  end_date: string;
  complete_date: string;
}

export interface SprintsList {
  sprints: SprintSummary[];
  board_id: number;
  _cached_at: string;
  from_cache: boolean;
}

export interface SprintDetail {
  sprint: SprintSummary;
  issues: JiraIssueSummary[];
  _cached_at: string;
  from_cache: boolean;
}

export interface BacklogData {
  issues: JiraIssueSummary[];
  _cached_at: string;
  from_cache: boolean;
}

export interface RefreshIssuesResult {
  project_key: string;
  issues_total: number;
  issues_refreshed: number;
  errors: { key: string; error: string }[];
}

@Injectable({ providedIn: 'root' })
export class JiraService {
  private http = inject(HttpClient);

  getProjects(): Observable<JiraProject[]> {
    return this.http.get<JiraProject[]>('/api/jira/projects');
  }

  getSprint(projectKey: string, refresh = false): Observable<SprintBoard> {
    return this.http.get<SprintBoard>(`/api/jira/projects/${projectKey}/sprint${refresh ? '?refresh=true' : ''}`);
  }

  getIssue(issueKey: string, refresh = false): Observable<JiraIssue> {
    return this.http.get<JiraIssue>(`/api/jira/issues/${issueKey}${refresh ? '?refresh=true' : ''}`);
  }

  getProjectMetadata(projectKey: string, refresh = false): Observable<JiraProjectMetadata> {
    return this.http.get<JiraProjectMetadata>(`/api/jira/projects/${projectKey}/metadata${refresh ? '?refresh=true' : ''}`);
  }

  listSprints(projectKey: string, refresh = false): Observable<SprintsList> {
    return this.http.get<SprintsList>(`/api/jira/projects/${projectKey}/sprints${refresh ? '?refresh=true' : ''}`);
  }

  getSprintById(projectKey: string, sprintId: number, refresh = false): Observable<SprintDetail> {
    return this.http.get<SprintDetail>(`/api/jira/projects/${projectKey}/sprints/${sprintId}${refresh ? '?refresh=true' : ''}`);
  }

  getBacklog(projectKey: string, refresh = false): Observable<BacklogData> {
    return this.http.get<BacklogData>(`/api/jira/projects/${projectKey}/backlog${refresh ? '?refresh=true' : ''}`);
  }

  refreshIssues(projectKey: string, issueKeys: string[]): Observable<RefreshIssuesResult> {
    return this.http.post<RefreshIssuesResult>(
      `/api/jira/projects/${projectKey}/refresh-issues`,
      { issue_keys: issueKeys },
    );
  }
}

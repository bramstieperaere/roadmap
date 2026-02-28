import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

export interface ContextItemEntry {
  type: string;        // "confluence_page" | "jira_issue" | "instructions" | "git_repo" | "repo_file" | "mixin"
  id: string;
  title: string;
  label: string;
  space_key?: string;
  project_key?: string;
  text?: string;       // for instructions
  path?: string;       // for git_repo
  repo_name?: string;  // for repo_file
  file_path?: string;  // for repo_file
}

export interface ChildContext {
  name: string;
  items: ContextItemEntry[];
}

export interface ContextItem {
  name: string;
  items: ContextItemEntry[];
  children: ChildContext[];
}

export interface RepoInfo {
  name: string;
  path: string;
}

export interface RepoTreeEntry {
  name: string;
  path: string;
  type: 'file' | 'dir';
}

export interface PreviewSection {
  type: string;
  id: string;
  label: string;
  content: string;
}

export interface ContributingContext {
  path: string;
  name: string;
  items: ContextItemEntry[];
  source: 'mixin' | 'mixin-parent';
}

@Injectable({ providedIn: 'root' })
export class ContextsService {
  private http = inject(HttpClient);

  getAll(): Observable<ContextItem[]> {
    return this.http.get<ContextItem[]>('/api/contexts');
  }

  get(name: string): Observable<ContextItem> {
    return this.http.get<ContextItem>(`/api/contexts/${encodeURIComponent(name)}`);
  }

  add(name: string): Observable<ContextItem> {
    return this.http.post<ContextItem>('/api/contexts', { name });
  }

  remove(name: string, force = false): Observable<{ status: string }> {
    return this.http.delete<{ status: string }>(
      `/api/contexts/${encodeURIComponent(name)}`,
      { params: force ? { force: 'true' } : {} },
    );
  }

  rename(oldName: string, newName: string): Observable<ContextItem> {
    return this.http.put<ContextItem>(
      `/api/contexts/${encodeURIComponent(oldName)}/rename`,
      { new_name: newName },
    );
  }

  addItem(contextName: string, type: string, id: string, label?: string, text?: string): Observable<ContextItemEntry> {
    return this.http.post<ContextItemEntry>(
      `/api/contexts/${encodeURIComponent(contextName)}/items`,
      { type, id, label, text },
    );
  }

  updateItem(contextName: string, type: string, id: string, body: { label?: string; text?: string }): Observable<ContextItemEntry> {
    return this.http.put<ContextItemEntry>(
      `/api/contexts/${encodeURIComponent(contextName)}/items/${type}/${encodeURIComponent(id)}`,
      body,
    );
  }

  removeItem(contextName: string, type: string, id: string): Observable<{ status: string }> {
    return this.http.delete<{ status: string }>(
      `/api/contexts/${encodeURIComponent(contextName)}/items/${type}/${encodeURIComponent(id)}`,
    );
  }

  moveItem(contextName: string, body: {
    type: string; id: string;
    from_child: string | null; to_child: string | null;
    to_index: number;
  }): Observable<ContextItem> {
    return this.http.put<ContextItem>(
      `/api/contexts/${encodeURIComponent(contextName)}/items/move`,
      body,
    );
  }

  reorderItems(contextName: string, items: { type: string; id: string }[]): Observable<ContextItem> {
    return this.http.put<ContextItem>(
      `/api/contexts/${encodeURIComponent(contextName)}/items/reorder`,
      { items },
    );
  }

  // ── Sub-context (children) ──

  addChild(parentName: string, childName: string): Observable<ChildContext> {
    return this.http.post<ChildContext>(
      `/api/contexts/${encodeURIComponent(parentName)}/children`,
      { name: childName },
    );
  }

  renameChild(parentName: string, oldName: string, newName: string): Observable<ChildContext> {
    return this.http.put<ChildContext>(
      `/api/contexts/${encodeURIComponent(parentName)}/children/${encodeURIComponent(oldName)}/rename`,
      { new_name: newName },
    );
  }

  removeChild(parentName: string, childName: string, force = false): Observable<{ status: string }> {
    return this.http.delete<{ status: string }>(
      `/api/contexts/${encodeURIComponent(parentName)}/children/${encodeURIComponent(childName)}`,
      { params: force ? { force: 'true' } : {} },
    );
  }

  addChildItem(parentName: string, childName: string, type: string, id: string, label?: string, text?: string): Observable<ContextItemEntry> {
    return this.http.post<ContextItemEntry>(
      `/api/contexts/${encodeURIComponent(parentName)}/children/${encodeURIComponent(childName)}/items`,
      { type, id, label, text },
    );
  }

  updateChildItem(parentName: string, childName: string, type: string, id: string, body: { label?: string; text?: string }): Observable<ContextItemEntry> {
    return this.http.put<ContextItemEntry>(
      `/api/contexts/${encodeURIComponent(parentName)}/children/${encodeURIComponent(childName)}/items/${type}/${encodeURIComponent(id)}`,
      body,
    );
  }

  removeChildItem(parentName: string, childName: string, type: string, id: string): Observable<{ status: string }> {
    return this.http.delete<{ status: string }>(
      `/api/contexts/${encodeURIComponent(parentName)}/children/${encodeURIComponent(childName)}/items/${type}/${encodeURIComponent(id)}`,
    );
  }

  reorderChildItems(parentName: string, childName: string, items: { type: string; id: string }[]): Observable<ContextItem> {
    return this.http.put<ContextItem>(
      `/api/contexts/${encodeURIComponent(parentName)}/children/${encodeURIComponent(childName)}/items/reorder`,
      { items },
    );
  }

  getChildPreview(parentName: string, childName: string): Observable<PreviewSection[]> {
    return this.http.get<PreviewSection[]>(
      `/api/contexts/meta/preview/${encodeURIComponent(parentName)}/${encodeURIComponent(childName)}`,
    );
  }

  getRepositories(): Observable<RepoInfo[]> {
    return this.http.get<RepoInfo[]>('/api/contexts/meta/repositories');
  }

  getPreview(name: string): Observable<PreviewSection[]> {
    return this.http.get<PreviewSection[]>(`/api/contexts/meta/preview/${encodeURIComponent(name)}`);
  }

  getRepoTree(repoName: string, path: string = ''): Observable<RepoTreeEntry[]> {
    return this.http.get<RepoTreeEntry[]>(
      `/api/contexts/meta/repo-tree/${encodeURIComponent(repoName)}`,
      { params: path ? { path } : {} },
    );
  }

  getAllPaths(): Observable<string[]> {
    return this.http.get<string[]>('/api/contexts/meta/all-paths');
  }

  getContributing(name: string): Observable<ContributingContext[]> {
    return this.http.get<ContributingContext[]>(
      `/api/contexts/meta/contributing/${encodeURIComponent(name)}`,
    );
  }

  getContributingChild(parentName: string, childName: string): Observable<ContributingContext[]> {
    return this.http.get<ContributingContext[]>(
      `/api/contexts/meta/contributing/${encodeURIComponent(parentName)}/${encodeURIComponent(childName)}`,
    );
  }
}

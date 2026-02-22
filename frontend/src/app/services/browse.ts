import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

export interface SearchResult {
  id: string;
  labels: string[];
  name: string;
  detail: string;
}

export interface TreeChild {
  id: string;
  labels: string[];
  name: string;
  has_children: boolean;
  kind: string;
}

@Injectable({ providedIn: 'root' })
export class BrowseService {
  private http = inject(HttpClient);

  searchNodes(q: string, limit = 20): Observable<SearchResult[]> {
    return this.http.get<SearchResult[]>('/api/browse/search', {
      params: { q, limit: limit.toString() },
    });
  }

  getTreeChildren(perspective: string, parentId?: string): Observable<TreeChild[]> {
    const params: Record<string, string> = { perspective };
    if (parentId) params['parent_id'] = parentId;
    return this.http.get<TreeChild[]>('/api/browse/tree', { params });
  }
}

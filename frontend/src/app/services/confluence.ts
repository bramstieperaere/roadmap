import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

export interface ConfluenceSpace {
  key: string;
  name: string;
}

export interface ConfluencePageSummary {
  id: string;
  title: string;
  parent_id: string | null;
  version: number;
  children: ConfluencePageSummary[];
}

export interface ConfluencePageTree {
  space_key: string;
  pages: ConfluencePageSummary[];
  total: number;
  _cached_at: string;
  from_cache: boolean;
}

export interface ConfluencePageAncestor {
  id: string;
  title: string;
}

export interface ConfluencePage {
  id: string;
  title: string;
  space_key: string;
  body_html: string;
  version: number;
  version_by: string;
  version_when: string;
  ancestors: ConfluencePageAncestor[];
  _cached_at: string;
  from_cache: boolean;
}

export interface RefreshSpaceResult {
  space_key: string;
  pages_total: number;
  pages_refreshed: number;
  errors: { id: string; title: string; error: string }[];
}

@Injectable({ providedIn: 'root' })
export class ConfluenceService {
  private http = inject(HttpClient);

  getSpaces(): Observable<ConfluenceSpace[]> {
    return this.http.get<ConfluenceSpace[]>('/api/confluence/spaces');
  }

  getPages(spaceKey: string, refresh = false): Observable<ConfluencePageTree> {
    return this.http.get<ConfluencePageTree>(
      `/api/confluence/spaces/${spaceKey}/pages${refresh ? '?refresh=true' : ''}`,
    );
  }

  getPage(pageId: string, refresh = false): Observable<ConfluencePage> {
    return this.http.get<ConfluencePage>(
      `/api/confluence/pages/${pageId}${refresh ? '?refresh=true' : ''}`,
    );
  }

  refreshSpace(spaceKey: string): Observable<RefreshSpaceResult> {
    return this.http.post<RefreshSpaceResult>(`/api/confluence/spaces/${spaceKey}/refresh`, {});
  }

  refreshPages(spaceKey: string, pageIds: string[]): Observable<RefreshSpaceResult> {
    return this.http.post<RefreshSpaceResult>(
      `/api/confluence/spaces/${spaceKey}/refresh-pages`,
      { page_ids: pageIds },
    );
  }
}

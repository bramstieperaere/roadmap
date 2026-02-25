import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

export interface FunctionalDocSection {
  heading: string;
  content: string;
  section_type: string;
  entities_mentioned: string[];
}

export interface FunctionalDocReference {
  ref_type: string;
  ref_id: string;
  label: string;
}

export interface FunctionalFieldMapping {
  source_system: string;
  source_field: string;
  target_system: string;
  target_field: string;
  transform: string | null;
  remarks: string | null;
}

export interface FunctionalDoc {
  source_id: string;
  source_type: string;
  space_key: string;
  title: string;
  ancestors: { id: string; title: string }[];
  version: number;
  version_by: string;
  version_when: string;
  processed_at: string;
  doc_type: string;
  domain: string;
  summary: string;
  tags: string[];
  sections: FunctionalDocSection[];
  references: FunctionalDocReference[];
  field_mappings: FunctionalFieldMapping[];
  metadata: Record<string, string>;
}

export interface FunctionalIndexEntry {
  page_id: string;
  title: string;
  doc_type: string;
  domain: string;
  summary: string;
}

export interface FunctionalIndex {
  space_key: string;
  processed_at: string;
  total_pages: number;
  processed: number;
  errors: number;
  documents: FunctionalIndexEntry[];
}

export interface ProcessResponse {
  job_id: string;
  message: string;
}

@Injectable({ providedIn: 'root' })
export class FunctionalService {
  private http = inject(HttpClient);

  processPages(spaceKey: string, pageIds?: string[]): Observable<ProcessResponse> {
    return this.http.post<ProcessResponse>('/api/functional/process', {
      space_key: spaceKey,
      page_ids: pageIds ?? null,
    });
  }

  getIndex(spaceKey: string): Observable<FunctionalIndex> {
    return this.http.get<FunctionalIndex>(`/api/functional/spaces/${spaceKey}/index`);
  }

  getDoc(spaceKey: string, pageId: string): Observable<FunctionalDoc> {
    return this.http.get<FunctionalDoc>(`/api/functional/docs/${spaceKey}/${pageId}`);
  }
}

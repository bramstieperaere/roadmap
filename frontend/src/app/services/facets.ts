import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

export interface FacetValueNode {
  id: string;
  name: string;
  label: string;
  ordinal: number;
  children: FacetValueNode[];
}

export interface FacetSummary {
  id: string;
  name: string;
  description: string;
  value_count: number;
}

export interface FacetDetail {
  id: string;
  name: string;
  description: string;
  values: FacetValueNode[];
}

export interface ClassificationEntry {
  facet_name: string;
  value_name: string;
  value_label: string;
}

export interface ClassifiedNode {
  node_id: string;
  labels: string[];
  name: string;
}

@Injectable({ providedIn: 'root' })
export class FacetsService {
  private http = inject(HttpClient);

  getAll(): Observable<FacetSummary[]> {
    return this.http.get<FacetSummary[]>('/api/facets');
  }

  create(name: string, description: string): Observable<FacetDetail> {
    return this.http.post<FacetDetail>('/api/facets', { name, description });
  }

  get(name: string): Observable<FacetDetail> {
    return this.http.get<FacetDetail>(`/api/facets/${encodeURIComponent(name)}`);
  }

  update(name: string, body: { name?: string; description?: string }): Observable<FacetDetail> {
    return this.http.put<FacetDetail>(`/api/facets/${encodeURIComponent(name)}`, body);
  }

  remove(name: string): Observable<{ status: string }> {
    return this.http.delete<{ status: string }>(`/api/facets/${encodeURIComponent(name)}`);
  }

  addValue(facetName: string, body: { name: string; label?: string; ordinal?: number }): Observable<FacetDetail> {
    return this.http.post<FacetDetail>(`/api/facets/${encodeURIComponent(facetName)}/values`, body);
  }

  addNarrower(facetName: string, parentName: string, body: { name: string; label?: string; ordinal?: number }): Observable<FacetDetail> {
    return this.http.post<FacetDetail>(
      `/api/facets/${encodeURIComponent(facetName)}/values/${encodeURIComponent(parentName)}/narrower`, body);
  }

  updateValue(facetName: string, valueName: string, body: { label?: string; ordinal?: number }): Observable<FacetDetail> {
    return this.http.put<FacetDetail>(
      `/api/facets/${encodeURIComponent(facetName)}/values/${encodeURIComponent(valueName)}`, body);
  }

  removeValue(facetName: string, valueName: string): Observable<FacetDetail> {
    return this.http.delete<FacetDetail>(
      `/api/facets/${encodeURIComponent(facetName)}/values/${encodeURIComponent(valueName)}`);
  }

  classify(nodeId: string, facetName: string, valueName: string): Observable<{ status: string }> {
    return this.http.post<{ status: string }>('/api/facets/classify', {
      node_id: nodeId, facet_name: facetName, value_name: valueName,
    });
  }

  unclassify(nodeId: string, facetName: string, valueName: string): Observable<{ status: string }> {
    return this.http.request<{ status: string }>('DELETE', '/api/facets/classify', {
      body: { node_id: nodeId, facet_name: facetName, value_name: valueName },
    });
  }

  getClassifications(nodeId: string): Observable<ClassificationEntry[]> {
    return this.http.get<ClassificationEntry[]>(`/api/facets/classifications/${encodeURIComponent(nodeId)}`);
  }

  getClassifiedNodes(facetName: string, valueName: string): Observable<ClassifiedNode[]> {
    return this.http.get<ClassifiedNode[]>(
      `/api/facets/${encodeURIComponent(facetName)}/values/${encodeURIComponent(valueName)}/classified`);
  }
}

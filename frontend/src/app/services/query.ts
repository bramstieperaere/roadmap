import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

export interface GraphNode {
  id: string;
  labels: string[];
  properties: Record<string, unknown>;
}

export interface GraphRelationship {
  id: string;
  type: string;
  start_node_id: string;
  end_node_id: string;
  properties: Record<string, unknown>;
}

export interface QueryResponse {
  cypher: string;
  nodes: GraphNode[];
  relationships: GraphRelationship[];
  error: string | null;
}

@Injectable({ providedIn: 'root' })
export class QueryService {
  private http = inject(HttpClient);

  executeQuery(question: string): Observable<QueryResponse> {
    return this.http.post<QueryResponse>('/api/query', { question });
  }

  expandNode(nodeId: string, operation: string, depth = 3): Observable<QueryResponse> {
    return this.http.post<QueryResponse>('/api/query/expand', {
      node_id: nodeId,
      operation,
      depth,
    });
  }
}

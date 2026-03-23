import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

export interface ServiceCard {
  name: string;
  is_external: boolean;
  inbound_count: number;
  outbound_count: number;
  queue_count: number;
  database_count: number;
}

export interface EndpointItem {
  path: string;
  http_method: string;
}

export interface EndpointGroup {
  group_name: string;
  base_path: string;
  endpoints: EndpointItem[];
}

export interface QueueItem {
  name: string;
  type: string;
  direction: string;
}

export interface DatabaseItem {
  name: string;
  technology: string;
  access: string[];
}

export interface ServiceDetail {
  name: string;
  is_external: boolean;
  inbound_groups: EndpointGroup[];
  outbound_groups: EndpointGroup[];
  queues: QueueItem[];
  databases: DatabaseItem[];
}

export interface DataModelInfo {
  name: string;
  full_name: string;
  kind: string;
}

export interface RepositoryInfo {
  name: string;
  entity_type: string;
}

export interface DatabaseWithRepos {
  name: string;
  technology: string;
  repositories: RepositoryInfo[];
}

export interface EndpointFlowDetail {
  path: string;
  http_method: string;
  controller_name: string;
  method_name: string;
  request_models: DataModelInfo[];
  response_models: DataModelInfo[];
  outbound_groups: EndpointGroup[];
  databases: DatabaseWithRepos[];
  queues: QueueItem[];
}

@Injectable({ providedIn: 'root' })
export class DataFlowService {
  private http = inject(HttpClient);

  getServices(): Observable<ServiceCard[]> {
    return this.http.get<ServiceCard[]>('/api/data-flow/services');
  }

  getServiceDetail(name: string): Observable<ServiceDetail> {
    return this.http.get<ServiceDetail>(
      `/api/data-flow/services/${encodeURIComponent(name)}`
    );
  }

  getEndpointFlow(serviceName: string, path: string, method: string): Observable<EndpointFlowDetail> {
    return this.http.get<EndpointFlowDetail>(
      `/api/data-flow/services/${encodeURIComponent(serviceName)}/endpoint-flow`,
      { params: { path, method } },
    );
  }
}

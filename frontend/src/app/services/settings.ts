import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

export interface Neo4jConfig {
  uri: string;
  username: string;
  password: string;
  database: string;
}

export interface ModuleConfig {
  name: string;
  type: 'java' | 'angular';
  relative_path: string;
  technologies: string[];
}

export const KNOWN_TECHNOLOGIES: Record<string, { label: string; types: string[] }> = {
  'spring-web': { label: 'Spring Web', types: ['java'] },
  'spring-jms': { label: 'Spring JMS', types: ['java'] },
};

export interface RepositoryConfig {
  name: string;
  path: string;
  modules: ModuleConfig[];
}

export interface AIProviderConfig {
  name: string;
  base_url: string;
  api_key: string;
  default_model: string;
}

export interface AITaskConfig {
  task_type: string;
  provider_name: string;
}

export interface AppConfig {
  neo4j: Neo4jConfig;
  repositories: RepositoryConfig[];
  ai_providers: AIProviderConfig[];
  ai_tasks: AITaskConfig[];
  encryption_salt?: string | null;
}

export interface TestConnectionResult {
  status: string;
  message: string;
}

export interface AnalyzeResponse {
  modules: ModuleConfig[];
}

@Injectable({
  providedIn: 'root',
})
export class SettingsService {
  private http = inject(HttpClient);

  getSettings(): Observable<AppConfig> {
    return this.http.get<AppConfig>('/api/settings');
  }

  updateSettings(config: AppConfig): Observable<AppConfig> {
    return this.http.put<AppConfig>('/api/settings', config);
  }

  testConnection(): Observable<TestConnectionResult> {
    return this.http.post<TestConnectionResult>('/api/settings/test-connection', {});
  }

  analyzeRepository(repoIndex: number): Observable<AnalyzeResponse> {
    return this.http.post<AnalyzeResponse>('/api/analysis/analyze', { repo_index: repoIndex });
  }
}

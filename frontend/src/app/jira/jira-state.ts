import { Injectable, signal } from '@angular/core';
import { JiraProject } from '../services/jira';

@Injectable({ providedIn: 'root' })
export class JiraStateService {
  projects = signal<JiraProject[]>([]);
  loadingProjects = signal(true);
  selectedProjectKey = signal<string>('');
}

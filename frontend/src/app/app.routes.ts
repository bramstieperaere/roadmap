import { Routes } from '@angular/router';
import { Home } from './home/home';
import { SettingsComponent } from './settings/settings';
import { JobsListComponent } from './jobs/jobs-list';
import { JobDetailComponent } from './jobs/job-detail';

export const routes: Routes = [
  { path: '', redirectTo: 'query', pathMatch: 'full' },
  { path: 'home', component: Home },
  { path: 'settings', component: SettingsComponent },
  { path: 'jobs', component: JobsListComponent },
  { path: 'jobs/:id', component: JobDetailComponent },
  { path: 'query', loadComponent: () => import('./query/query').then(m => m.QueryComponent) },
  { path: 'sequence', loadComponent: () => import('./sequence/sequence').then(m => m.SequenceComponent) },
  {
    path: 'jira',
    loadComponent: () => import('./jira/jira-shell').then(m => m.JiraShellComponent),
    children: [
      { path: '', redirectTo: 'sprint', pathMatch: 'full' },
      { path: 'sprint', loadComponent: () => import('./jira/jira-sprint').then(m => m.JiraSprintComponent) },
      { path: 'sprints', loadComponent: () => import('./jira/jira-sprints').then(m => m.JiraSprintsComponent) },
      { path: 'sprints/:id', loadComponent: () => import('./jira/jira-sprint-detail').then(m => m.JiraSprintDetailComponent) },
      { path: 'backlog', loadComponent: () => import('./jira/jira-backlog').then(m => m.JiraBacklogComponent) },
      { path: 'metadata', loadComponent: () => import('./jira/jira-metadata').then(m => m.JiraMetadataComponent) },
      { path: 'issue/:key', loadComponent: () => import('./jira/jira-issue').then(m => m.JiraIssueComponent) },
    ],
  },
  {
    path: 'confluence',
    loadComponent: () => import('./confluence/confluence-shell').then(m => m.ConfluenceSpacesComponent),
  },
  {
    path: 'confluence/:spaceKey',
    loadComponent: () => import('./confluence/confluence-shell').then(m => m.ConfluenceSpacesComponent),
  },
  {
    path: 'confluence/:spaceKey/page/:id',
    loadComponent: () => import('./confluence/confluence-page').then(m => m.ConfluencePageComponent),
  },
  {
    path: 'functional',
    loadComponent: () => import('./functional/functional-viewer').then(m => m.FunctionalViewerComponent),
  },
  {
    path: 'functional/:spaceKey',
    loadComponent: () => import('./functional/functional-viewer').then(m => m.FunctionalViewerComponent),
  },
  {
    path: 'functional/:spaceKey/:pageId',
    loadComponent: () => import('./functional/functional-viewer').then(m => m.FunctionalViewerComponent),
  },
];

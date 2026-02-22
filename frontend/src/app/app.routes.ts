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
];

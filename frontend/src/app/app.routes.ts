import { Routes } from '@angular/router';
import { Home } from './home/home';
import { SettingsComponent } from './settings/settings';
import { JobsListComponent } from './jobs/jobs-list';
import { JobDetailComponent } from './jobs/job-detail';

export const routes: Routes = [
  { path: '', component: Home },
  { path: 'settings', component: SettingsComponent },
  { path: 'jobs', component: JobsListComponent },
  { path: 'jobs/:id', component: JobDetailComponent },
];

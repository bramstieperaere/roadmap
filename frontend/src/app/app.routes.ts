import { Routes } from '@angular/router';
import { Home } from './home/home';
import { SettingsComponent } from './settings/settings';

export const routes: Routes = [
  { path: '', component: Home },
  { path: 'settings', component: SettingsComponent },
];

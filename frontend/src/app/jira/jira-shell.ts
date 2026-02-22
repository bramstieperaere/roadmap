import { Component, inject, OnInit } from '@angular/core';
import { RouterOutlet, RouterLink, RouterLinkActive } from '@angular/router';
import { JiraService } from '../services/jira';
import { JiraStateService } from './jira-state';

@Component({
  selector: 'app-jira-shell',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  templateUrl: './jira-shell.html',
  styleUrl: './jira-shell.scss',
})
export class JiraShellComponent implements OnInit {
  private jiraService = inject(JiraService);
  state = inject(JiraStateService);

  ngOnInit() {
    if (this.state.projects().length === 0) {
      this.jiraService.getProjects().subscribe({
        next: (projects) => {
          this.state.projects.set(projects);
          this.state.loadingProjects.set(false);
          if (projects.length > 0 && !this.state.selectedProjectKey()) {
            this.state.selectedProjectKey.set(projects[0].key);
          }
        },
        error: () => {
          this.state.loadingProjects.set(false);
        },
      });
    }
  }

  selectProject(key: string) {
    this.state.selectedProjectKey.set(key);
  }
}

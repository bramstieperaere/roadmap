import { Component, inject, input, OnInit, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { SettingsService } from '../../services/settings';

@Component({
  selector: 'app-ai-task-status',
  standalone: true,
  imports: [RouterLink],
  template: `
    @if (configured()) {
      <span class="text-muted small">
        via <strong>{{ providerName() }}</strong>
        &mdash; <a routerLink="/settings" [queryParams]="{tab: 'tasks'}" class="ai-task-link">change</a>
      </span>
    } @else {
      <span class="text-muted small">
        No AI provider configured &mdash;
        <a routerLink="/settings" [queryParams]="{tab: 'tasks'}" class="ai-task-link">set up in Settings</a>
      </span>
    }
  `,
  styles: [`
    .ai-task-link {
      color: var(--rm-primary, #1a3a5c);
      text-decoration: underline;
      font-weight: 500;
    }
  `],
})
export class AiTaskStatus implements OnInit {
  private settingsService = inject(SettingsService);

  /** The AI task type to check, e.g. 'cypher_generation'. */
  taskType = input.required<string>();

  /** Whether a provider is configured for this task. */
  configured = signal(false);

  /** Name of the resolved provider. */
  providerName = signal('');

  ngOnInit() {
    this.settingsService.getSettings().subscribe({
      next: (cfg) => {
        const task = cfg.ai_tasks.find(t => t.task_type === this.taskType() && t.provider_name);
        if (task) {
          const provider = cfg.ai_providers.find(p => p.name === task.provider_name);
          if (provider) {
            this.providerName.set(provider.name);
            this.configured.set(true);
            return;
          }
        }
        this.configured.set(false);
      },
      error: () => {},
    });
  }
}

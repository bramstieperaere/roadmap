import { Component, inject, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { SettingsService, AppConfig, ModuleConfig, AIProviderConfig } from '../services/settings';
import { JobsService } from '../services/jobs';

@Component({
  selector: 'app-settings',
  imports: [FormsModule],
  templateUrl: './settings.html',
  styleUrl: './settings.scss',
})
export class SettingsComponent implements OnInit {
  private settingsService = inject(SettingsService);
  private jobsService = inject(JobsService);
  private router = inject(Router);

  config = signal<AppConfig>({
    neo4j: { uri: '', username: '', password: '', database: '' },
    repositories: [],
    ai_providers: [],
    ai_tasks: [],
  });
  testingConnection = signal(false);
  analyzing = signal<number | null>(null);
  message = signal<{ text: string; type: 'success' | 'danger' } | null>(null);

  ngOnInit() {
    this.settingsService.getSettings().subscribe({
      next: (config) => this.config.set(config),
      error: () => this.showMessage('Failed to load settings', 'danger'),
    });
  }

  save() {
    this.settingsService.updateSettings(this.config()).subscribe({
      next: (config) => {
        this.config.set(config);
        this.showMessage('Settings saved successfully', 'success');
      },
      error: () => this.showMessage('Failed to save settings', 'danger'),
    });
  }

  testConnection() {
    this.testingConnection.set(true);
    this.settingsService.testConnection().subscribe({
      next: (result) => {
        this.testingConnection.set(false);
        this.showMessage(result.message, 'success');
      },
      error: (err) => {
        this.testingConnection.set(false);
        this.showMessage(err.error?.detail || 'Connection failed', 'danger');
      },
    });
  }

  // Repositories
  addRepository() {
    this.config.update((c) => ({
      ...c,
      repositories: [...c.repositories, { path: '', modules: [] }],
    }));
  }

  removeRepository(index: number) {
    this.config.update((c) => ({
      ...c,
      repositories: c.repositories.filter((_, i) => i !== index),
    }));
  }

  addModule(repoIndex: number) {
    this.config.update((c) => {
      const repos = [...c.repositories];
      repos[repoIndex] = {
        ...repos[repoIndex],
        modules: [...repos[repoIndex].modules, { name: '', type: 'java', relative_path: '' }],
      };
      return { ...c, repositories: repos };
    });
  }

  removeModule(repoIndex: number, moduleIndex: number) {
    this.config.update((c) => {
      const repos = [...c.repositories];
      repos[repoIndex] = {
        ...repos[repoIndex],
        modules: repos[repoIndex].modules.filter((_, i) => i !== moduleIndex),
      };
      return { ...c, repositories: repos };
    });
  }

  updateNeo4jField(field: string, value: string) {
    this.config.update((c) => ({
      ...c,
      neo4j: { ...c.neo4j, [field]: value },
    }));
  }

  updateRepoPath(index: number, path: string) {
    this.config.update((c) => {
      const repos = [...c.repositories];
      repos[index] = { ...repos[index], path };
      return { ...c, repositories: repos };
    });
  }

  updateModule(repoIndex: number, moduleIndex: number, field: keyof ModuleConfig, value: string) {
    this.config.update((c) => {
      const repos = [...c.repositories];
      const modules = [...repos[repoIndex].modules];
      modules[moduleIndex] = { ...modules[moduleIndex], [field]: value };
      repos[repoIndex] = { ...repos[repoIndex], modules };
      return { ...c, repositories: repos };
    });
  }

  // AI Providers
  addAIProvider() {
    this.config.update((c) => ({
      ...c,
      ai_providers: [...c.ai_providers, { name: '', base_url: 'https://api.openai.com/v1', api_key: '', default_model: 'gpt-4o' }],
    }));
  }

  removeAIProvider(index: number) {
    this.config.update((c) => ({
      ...c,
      ai_providers: c.ai_providers.filter((_, i) => i !== index),
    }));
  }

  updateAIProvider(index: number, field: keyof AIProviderConfig, value: string) {
    this.config.update((c) => {
      const providers = [...c.ai_providers];
      providers[index] = { ...providers[index], [field]: value };
      return { ...c, ai_providers: providers };
    });
  }

  // AI Tasks
  addAITask() {
    this.config.update((c) => ({
      ...c,
      ai_tasks: [...c.ai_tasks, { task_type: 'repository_analysis', provider_name: '' }],
    }));
  }

  removeAITask(index: number) {
    this.config.update((c) => ({
      ...c,
      ai_tasks: c.ai_tasks.filter((_, i) => i !== index),
    }));
  }

  updateAITask(index: number, field: string, value: string) {
    this.config.update((c) => {
      const tasks = [...c.ai_tasks];
      tasks[index] = { ...tasks[index], [field]: value };
      return { ...c, ai_tasks: tasks };
    });
  }

  // Analyze
  analyzeRepository(repoIndex: number) {
    this.analyzing.set(repoIndex);
    this.settingsService.analyzeRepository(repoIndex).subscribe({
      next: (result) => {
        this.analyzing.set(null);
        this.config.update((c) => {
          const repos = [...c.repositories];
          repos[repoIndex] = { ...repos[repoIndex], modules: result.modules };
          return { ...c, repositories: repos };
        });
        this.showMessage(`Analysis complete: ${result.modules.length} module(s) detected`, 'success');
      },
      error: (err) => {
        this.analyzing.set(null);
        this.showMessage(err.error?.detail || 'Analysis failed', 'danger');
      },
    });
  }

  startAnalysisJob(repoIndex: number) {
    const repo = this.config().repositories[repoIndex];
    const javaModules = repo.modules
      .map((m, i) => ({ module: m, index: i }))
      .filter(({ module }) => module.type === 'java');

    if (javaModules.length === 0) {
      this.showMessage('No Java modules to analyze', 'danger');
      return;
    }

    let started = 0;
    for (const { index } of javaModules) {
      this.jobsService.startJob(repoIndex, index).subscribe({
        next: () => {
          started++;
          if (started === javaModules.length) {
            this.router.navigate(['/jobs']);
          }
        },
        error: (err) => {
          this.showMessage(err.error?.detail || 'Failed to start job', 'danger');
        },
      });
    }
  }

  dismissMessage() {
    this.message.set(null);
  }

  private showMessage(text: string, type: 'success' | 'danger') {
    this.message.set({ text, type });
    setTimeout(() => this.message.set(null), 4000);
  }
}

import { Component, inject, OnInit, signal, DestroyRef } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { SettingsService, AppConfig, ModuleConfig, AIProviderConfig, AtlassianConfig, JiraBoardOption, KNOWN_TECHNOLOGIES } from '../services/settings';
import { EncryptionService } from '../services/encryption';
import { JobsService } from '../services/jobs';

@Component({
  selector: 'app-settings',
  imports: [FormsModule, RouterLink],
  templateUrl: './settings.html',
  styleUrl: './settings.scss',
})
export class SettingsComponent implements OnInit {
  private settingsService = inject(SettingsService);
  private encryptionService = inject(EncryptionService);
  private jobsService = inject(JobsService);
  private router = inject(Router);
  private destroyRef = inject(DestroyRef);

  config = signal<AppConfig>({
    neo4j: { uri: '', username: '', password: '', database: '' },
    atlassian: { deployment_type: 'cloud', base_url: '', email: '', api_token: '', jira_projects: [], confluence_spaces: [], cache_dir: '', refresh_duration: 3600 },
    repositories: [],
    ai_providers: [],
    ai_tasks: [],
  });
  testingConnection = signal(false);
  testingAtlassian = signal(false);
  lookingUpProject = signal(false);
  lookingUpSpace = signal(false);
  boardOptions = signal<JiraBoardOption[]>([]);
  analyzing = signal<number | null>(null);
  message = signal<{ text: string; type: 'success' | 'danger' } | null>(null);
  activeTab = signal<'neo4j' | 'atlassian' | 'repos' | 'providers' | 'tasks'>('neo4j');
  selectedRepoIndex = signal<number | null>(null);
  editingSection = signal<string | null>(null);
  private configBackup: AppConfig | null = null;

  ngOnInit() {
    this.loadSettings();
    this.encryptionService.unlocked$.pipe(
      takeUntilDestroyed(this.destroyRef),
    ).subscribe(() => this.loadSettings());
  }

  private loadSettings() {
    this.settingsService.getSettings().subscribe({
      next: (config) => {
        for (const repo of config.repositories) {
          if (!repo.name && repo.path) repo.name = deriveRepoName(repo.path);
        }
        if (!config.atlassian.confluence_spaces) {
          config.atlassian.confluence_spaces = [];
        }
        this.config.set(config);
      },
      error: () => this.showMessage('Failed to load settings', 'danger'),
    });
  }

  // Section editing
  startEditing(section: string) {
    this.configBackup = structuredClone(this.config());
    this.editingSection.set(section);
  }

  cancelEditing() {
    if (this.configBackup) {
      this.config.set(this.configBackup);
      const section = this.editingSection();
      if (section?.startsWith('repo-')) {
        const idx = parseInt(section.split('-')[1]);
        if (idx >= this.config().repositories.length) {
          this.selectedRepoIndex.set(
            this.config().repositories.length > 0 ? this.config().repositories.length - 1 : null,
          );
        }
      }
    }
    this.editingSection.set(null);
    this.configBackup = null;
  }

  saveSection() {
    this.editingSection.set(null);
    this.configBackup = null;
    this.persistConfig('Settings saved');
  }

  switchTab(tab: 'neo4j' | 'atlassian' | 'repos' | 'providers' | 'tasks') {
    if (this.editingSection()) this.cancelEditing();
    this.activeTab.set(tab);
  }

  selectRepo(index: number) {
    if (this.editingSection()?.startsWith('repo-') && this.editingSection() !== 'repo-' + index) {
      this.cancelEditing();
    }
    this.selectedRepoIndex.set(index);
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
    this.configBackup = structuredClone(this.config());
    this.config.update((c) => ({
      ...c,
      repositories: [...c.repositories, { name: '', path: '', modules: [] }],
    }));
    const newIndex = this.config().repositories.length - 1;
    this.selectedRepoIndex.set(newIndex);
    this.editingSection.set('repo-' + newIndex);
  }

  removeRepository(index: number) {
    this.config.update((c) => ({
      ...c,
      repositories: c.repositories.filter((_, i) => i !== index),
    }));
    const sel = this.selectedRepoIndex();
    if (sel === index) this.selectedRepoIndex.set(null);
    else if (sel !== null && sel > index) this.selectedRepoIndex.update((v) => v! - 1);
    this.editingSection.set(null);
    this.configBackup = null;
    this.persistConfig('Repository removed');
  }

  readonly knownTechnologies = KNOWN_TECHNOLOGIES;

  addModule(repoIndex: number) {
    this.config.update((c) => {
      const repos = [...c.repositories];
      repos[repoIndex] = {
        ...repos[repoIndex],
        modules: [...repos[repoIndex].modules, { name: '', type: 'java', relative_path: '', technologies: [] }],
      };
      return { ...c, repositories: repos };
    });
  }

  toggleTechnology(repoIndex: number, moduleIndex: number, tech: string) {
    this.config.update((c) => {
      const repos = [...c.repositories];
      const modules = [...repos[repoIndex].modules];
      const current = modules[moduleIndex].technologies || [];
      const technologies = current.includes(tech)
        ? current.filter((t) => t !== tech)
        : [...current, tech];
      modules[moduleIndex] = { ...modules[moduleIndex], technologies };
      repos[repoIndex] = { ...repos[repoIndex], modules };
      return { ...c, repositories: repos };
    });
  }

  technologiesForType(type: string): { key: string; label: string }[] {
    return Object.entries(KNOWN_TECHNOLOGIES)
      .filter(([, v]) => v.types.includes(type))
      .map(([key, v]) => ({ key, label: v.label }));
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

  updateAtlassianField(field: string, value: string) {
    this.config.update((c) => ({
      ...c,
      atlassian: { ...c.atlassian, [field]: value },
    }));
  }

  testAtlassianConnection() {
    this.testingAtlassian.set(true);
    this.settingsService.testAtlassianConnection().subscribe({
      next: (result) => {
        this.testingAtlassian.set(false);
        this.showMessage(result.message, 'success');
      },
      error: (err) => {
        this.testingAtlassian.set(false);
        this.showMessage(err.error?.detail || 'Connection failed', 'danger');
      },
    });
  }

  // Jira Projects
  addJiraProject() {
    this.configBackup = structuredClone(this.config());
    this.config.update((c) => ({
      ...c,
      atlassian: {
        ...c.atlassian,
        jira_projects: [...c.atlassian.jira_projects, { key: '', name: '', board_id: null }],
      },
    }));
    const newIndex = this.config().atlassian.jira_projects.length - 1;
    this.boardOptions.set([]);
    this.editingSection.set('jira-project-' + newIndex);
  }

  removeJiraProject(index: number) {
    this.config.update((c) => ({
      ...c,
      atlassian: {
        ...c.atlassian,
        jira_projects: c.atlassian.jira_projects.filter((_, i) => i !== index),
      },
    }));
    this.editingSection.set(null);
    this.configBackup = null;
    this.persistConfig('Project removed');
  }

  updateJiraProject(index: number, field: string, value: string | number | null) {
    this.config.update((c) => {
      const projects = [...c.atlassian.jira_projects];
      projects[index] = { ...projects[index], [field]: value };
      return { ...c, atlassian: { ...c.atlassian, jira_projects: projects } };
    });
  }

  lookupProject(index: number) {
    const key = this.config().atlassian.jira_projects[index]?.key;
    if (!key) {
      this.showMessage('Enter a project key first', 'danger');
      return;
    }
    this.lookingUpProject.set(true);
    this.boardOptions.set([]);
    this.settingsService.lookupJiraProject(key).subscribe({
      next: (result) => {
        this.lookingUpProject.set(false);
        this.updateJiraProject(index, 'name', result.name);
        this.boardOptions.set(result.boards);
        this.showMessage(`Project "${result.name}" verified (${result.boards.length} board(s))`, 'success');
      },
      error: (err) => {
        this.lookingUpProject.set(false);
        this.showMessage(err.error?.detail || 'Project not found', 'danger');
      },
    });
  }

  // Confluence Spaces
  addConfluenceSpace() {
    this.configBackup = structuredClone(this.config());
    this.config.update((c) => ({
      ...c,
      atlassian: {
        ...c.atlassian,
        confluence_spaces: [...c.atlassian.confluence_spaces, { key: '', name: '' }],
      },
    }));
    const newIndex = this.config().atlassian.confluence_spaces.length - 1;
    this.editingSection.set('confluence-space-' + newIndex);
  }

  removeConfluenceSpace(index: number) {
    this.config.update((c) => ({
      ...c,
      atlassian: {
        ...c.atlassian,
        confluence_spaces: c.atlassian.confluence_spaces.filter((_, i) => i !== index),
      },
    }));
    this.editingSection.set(null);
    this.configBackup = null;
    this.persistConfig('Space removed');
  }

  updateConfluenceSpace(index: number, field: string, value: string) {
    this.config.update((c) => {
      const spaces = [...c.atlassian.confluence_spaces];
      spaces[index] = { ...spaces[index], [field]: value };
      return { ...c, atlassian: { ...c.atlassian, confluence_spaces: spaces } };
    });
  }

  lookupSpace(index: number) {
    const key = this.config().atlassian.confluence_spaces[index]?.key;
    if (!key) {
      this.showMessage('Enter a space key first', 'danger');
      return;
    }
    this.lookingUpSpace.set(true);
    this.settingsService.lookupConfluenceSpace(key).subscribe({
      next: (result) => {
        this.lookingUpSpace.set(false);
        this.updateConfluenceSpace(index, 'name', result.name);
        this.showMessage(`Space "${result.name}" verified`, 'success');
      },
      error: (err) => {
        this.lookingUpSpace.set(false);
        this.showMessage(err.error?.detail || 'Space not found', 'danger');
      },
    });
  }

  updateRepoName(index: number, name: string) {
    this.config.update((c) => {
      const repos = [...c.repositories];
      repos[index] = { ...repos[index], name };
      return { ...c, repositories: repos };
    });
  }

  updateRepoPath(index: number, path: string) {
    this.config.update((c) => {
      const repos = [...c.repositories];
      const oldName = repos[index].name;
      const oldDerived = deriveRepoName(repos[index].path);
      const name = !oldName || oldName === oldDerived ? deriveRepoName(path) : oldName;
      repos[index] = { ...repos[index], path, name };
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
    this.configBackup = structuredClone(this.config());
    this.config.update((c) => ({
      ...c,
      ai_providers: [
        ...c.ai_providers,
        { name: '', base_url: 'https://api.openai.com/v1', api_key: '', default_model: 'gpt-4o' },
      ],
    }));
    this.editingSection.set('provider-' + (this.config().ai_providers.length - 1));
  }

  removeAIProvider(index: number) {
    this.config.update((c) => ({
      ...c,
      ai_providers: c.ai_providers.filter((_, i) => i !== index),
    }));
    this.editingSection.set(null);
    this.configBackup = null;
    this.persistConfig('Provider removed');
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
    this.configBackup = structuredClone(this.config());
    this.config.update((c) => ({
      ...c,
      ai_tasks: [...c.ai_tasks, { task_type: 'repository_analysis', provider_name: '' }],
    }));
    this.editingSection.set('task-' + (this.config().ai_tasks.length - 1));
  }

  removeAITask(index: number) {
    this.config.update((c) => ({
      ...c,
      ai_tasks: c.ai_tasks.filter((_, i) => i !== index),
    }));
    this.editingSection.set(null);
    this.configBackup = null;
    this.persistConfig('Task removed');
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
    if (!repo.modules.length) {
      this.showMessage('No modules to analyze', 'danger');
      return;
    }

    this.jobsService.startRepo(repoIndex).subscribe({
      next: () => this.router.navigate(['/jobs']),
      error: (err) => {
        this.showMessage(err.error?.detail || 'Failed to start analysis', 'danger');
      },
    });
  }

  dismissMessage() {
    this.message.set(null);
  }

  private persistConfig(successMessage: string) {
    this.settingsService.updateSettings(this.config()).subscribe({
      next: (config) => {
        for (const repo of config.repositories) {
          if (!repo.name && repo.path) repo.name = deriveRepoName(repo.path);
        }
        this.config.set(config);
        this.showMessage(successMessage, 'success');
      },
      error: () => this.showMessage('Failed to save settings', 'danger'),
    });
  }

  private showMessage(text: string, type: 'success' | 'danger') {
    this.message.set({ text, type });
    setTimeout(() => this.message.set(null), 4000);
  }
}

function deriveRepoName(path: string): string {
  if (!path) return '';
  const segments = path.replace(/\\/g, '/').replace(/\/+$/, '').split('/');
  return segments[segments.length - 1] || '';
}

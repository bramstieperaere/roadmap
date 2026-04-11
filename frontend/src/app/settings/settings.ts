import { Component, computed, effect, inject, OnInit, signal, DestroyRef } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { SettingsService, AppConfig, ModuleConfig, AIProviderConfig, JiraBoardOption, KNOWN_TECHNOLOGIES } from '../services/settings';
import { GitMiningService, ProcessorInfo } from '../services/git-mining';
import { EncryptionService } from '../services/encryption';
import { ConfirmDialogService } from '../components/confirm-dialog/confirm-dialog.service';
import { WhisperTextarea } from '../components/whisper-textarea/whisper-textarea';

@Component({
  selector: 'app-settings',
  imports: [FormsModule, RouterLink, WhisperTextarea],
  templateUrl: './settings.html',
  styleUrl: './settings.scss',
})
export class SettingsComponent implements OnInit {
  private settingsService = inject(SettingsService);
  private encryptionService = inject(EncryptionService);
  private gitMiningService = inject(GitMiningService);
  private destroyRef = inject(DestroyRef);
  private confirmDialog = inject(ConfirmDialogService);
  private route = inject(ActivatedRoute);

  config = signal<AppConfig>({
    neo4j: { uri: '', username: '', password: '', database: '' },
    atlassian: { deployment_type: 'cloud', base_url: '', email: '', api_token: '', bitbucket_username: '', bitbucket_app_password: '', jira_projects: [], confluence_spaces: [], cache_dir: '', refresh_duration: 3600 },
    repositories: [],
    ai_providers: [],
    ai_tasks: [],
    whisper: { base_url: 'https://api.openai.com/v1', api_key: '', model: 'whisper-1', postprocess_provider: '', postprocess_model: '' },
    logzio: { base_url: 'https://api.logz.io', api_token: '', default_size: 50 },
    scratch_base_dir: '',
    file_viewers: [],
  });
  testingConnection = signal(false);
  testingAtlassian = signal(false);
  testingBitbucket = signal(false);
  testingLogzio = signal(false);
  lookingUpProject = signal(false);
  lookingUpSpace = signal(false);
  boardOptions = signal<JiraBoardOption[]>([]);
  analyzing = signal<number | null>(null);
  message = signal<{ text: string; type: 'success' | 'danger' } | null>(null);
  processors = signal<ProcessorInfo[]>([]);
  activeTab = signal<'general' | 'viewers' | 'neo4j' | 'atlassian' | 'bitbucket' | 'repos' | 'providers' | 'tasks' | 'whisper' | 'logzio' | 'processors'>('general');
  selectedRepoIndex = signal<number | null>(null);
  editingSection = signal<string | null>(null);
  visibleSecrets = signal<Set<string>>(new Set());
  importing = signal(false);
  importParentPath = signal('');
  importFolders = signal<{ name: string; selected: boolean; alreadyAdded: boolean }[]>([]);
  tagFilter = signal<string[]>([]);
  tagInput = signal('');
  repoSearch = signal('');
  private configBackup: AppConfig | null = null;

  allTags = computed(() => {
    const tags = new Set<string>();
    for (const repo of this.config().repositories) {
      for (const t of repo.tags || []) tags.add(t);
    }
    return [...tags].sort();
  });

  filteredRepositories = computed(() => {
    const active = this.tagFilter();
    const search = this.repoSearch().toLowerCase().trim();
    const repos = this.config().repositories;
    let indexed = repos.map((repo, index) => ({ repo, index }));
    if (active.length > 0) {
      indexed = indexed.filter(({ repo }) =>
        active.every(tag => (repo.tags || []).includes(tag)),
      );
    }
    if (search) {
      indexed = indexed.filter(({ repo }) =>
        (repo.name || '').toLowerCase().includes(search),
      );
    }
    return indexed;
  });

  constructor() {
    effect(() => {
      const items = this.filteredRepositories();
      const current = this.selectedRepoIndex();
      if (items.length > 0 && (current === null || !items.some(i => i.index === current))) {
        this.selectedRepoIndex.set(items[0].index);
      } else if (items.length === 0) {
        this.selectedRepoIndex.set(null);
      }
    });
  }

  ngOnInit() {
    this.loadSettings();
    this.encryptionService.unlocked$.pipe(
      takeUntilDestroyed(this.destroyRef),
    ).subscribe(() => this.loadSettings());

    this.gitMiningService.getProcessors().subscribe({
      next: p => this.processors.set(p),
    });

    const tab = this.route.snapshot.queryParamMap.get('tab');
    if (tab && ['general', 'viewers', 'neo4j', 'atlassian', 'bitbucket', 'repos', 'providers', 'tasks', 'whisper', 'logzio', 'processors'].includes(tab)) {
      this.activeTab.set(tab as any);
    }
  }

  private loadSettings() {
    this.settingsService.getSettings().subscribe({
      next: (config) => {
        for (const repo of config.repositories) {
          if (!repo.name && repo.path) repo.name = deriveRepoName(repo.path);
        }
        config.repositories.sort((a, b) => a.name.localeCompare(b.name));
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

  switchTab(tab: 'general' | 'viewers' | 'neo4j' | 'atlassian' | 'bitbucket' | 'repos' | 'providers' | 'tasks' | 'whisper' | 'logzio' | 'processors') {
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
      repositories: [...c.repositories, { name: '', path: '', tags: [], modules: [], processors: [] }],
    }));
    const newIndex = this.config().repositories.length - 1;
    this.selectedRepoIndex.set(newIndex);
    this.editingSection.set('repo-' + newIndex);
  }

  async removeRepository(index: number) {
    const name = this.config().repositories[index]?.name || 'this repository';
    const ok = await this.confirmDialog.open({ title: 'Remove repository', message: `Remove "${name}"?` });
    if (!ok) return;
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

  async removeModule(repoIndex: number, moduleIndex: number) {
    const name = this.config().repositories[repoIndex]?.modules[moduleIndex]?.name || 'this module';
    const ok = await this.confirmDialog.open({ title: 'Remove module', message: `Remove "${name}"?` });
    if (!ok) return;
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

  testBitbucketConnection() {
    this.testingBitbucket.set(true);
    this.settingsService.testBitbucketConnection().subscribe({
      next: (result) => {
        this.testingBitbucket.set(false);
        this.showMessage(result.message, 'success');
      },
      error: (err) => {
        this.testingBitbucket.set(false);
        this.showMessage(err.error?.detail || 'Bitbucket connection failed', 'danger');
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

  async removeJiraProject(index: number) {
    const key = this.config().atlassian.jira_projects[index]?.key || 'this project';
    const ok = await this.confirmDialog.open({ title: 'Remove Jira project', message: `Remove "${key}"?` });
    if (!ok) return;
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

  async removeConfluenceSpace(index: number) {
    const key = this.config().atlassian.confluence_spaces[index]?.key || 'this space';
    const ok = await this.confirmDialog.open({ title: 'Remove Confluence space', message: `Remove "${key}"?` });
    if (!ok) return;
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

  browseFolder(index: number) {
    const currentPath = this.config().repositories[index]?.path || '';
    this.settingsService.browseFolder(currentPath).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (res) => this.updateRepoPath(index, res.path),
    });
  }

  toggleTagFilter(tag: string) {
    this.tagFilter.update(tags =>
      tags.includes(tag) ? tags.filter(t => t !== tag) : [...tags, tag],
    );
  }

  clearTagFilter() {
    this.tagFilter.set([]);
  }

  addTagToRepo(repoIndex: number, tag: string) {
    const normalized = tag.trim().toLowerCase();
    if (!normalized) return;
    this.config.update(c => {
      const repos = [...c.repositories];
      const current = repos[repoIndex].tags || [];
      if (current.includes(normalized)) return c;
      repos[repoIndex] = { ...repos[repoIndex], tags: [...current, normalized] };
      return { ...c, repositories: repos };
    });
  }

  removeTagFromRepo(repoIndex: number, tag: string) {
    this.config.update(c => {
      const repos = [...c.repositories];
      repos[repoIndex] = {
        ...repos[repoIndex],
        tags: (repos[repoIndex].tags || []).filter(t => t !== tag),
      };
      return { ...c, repositories: repos };
    });
  }

  toggleProcessor(repoIndex: number, processorName: string) {
    this.config.update(c => {
      const repos = [...c.repositories];
      const current = repos[repoIndex].processors || [];
      const processors = current.includes(processorName)
        ? current.filter(p => p !== processorName)
        : [...current, processorName];
      repos[repoIndex] = { ...repos[repoIndex], processors };
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

  async removeAIProvider(index: number) {
    const name = this.config().ai_providers[index]?.name || 'this provider';
    const ok = await this.confirmDialog.open({ title: 'Remove AI provider', message: `Remove "${name}"?` });
    if (!ok) return;
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

  async removeAITask(index: number) {
    const type = this.config().ai_tasks[index]?.task_type || 'this task';
    const ok = await this.confirmDialog.open({ title: 'Remove AI task', message: `Remove "${type}"?` });
    if (!ok) return;
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

  // Whisper
  updateWhisper(field: string, value: string) {
    this.config.update((c) => ({
      ...c,
      whisper: { ...c.whisper, [field]: value },
    }));
  }

  // Logz.io
  updateLogzio(field: string, value: string | number) {
    this.config.update((c) => ({
      ...c,
      logzio: { ...c.logzio, [field]: value },
    }));
  }

  testLogzioConnection() {
    this.testingLogzio.set(true);
    this.settingsService.testLogzioConnection().subscribe({
      next: (result) => { this.testingLogzio.set(false); this.showMessage(result.message, 'success'); },
      error: (err) => { this.testingLogzio.set(false); this.showMessage(err.error?.detail || 'Logz.io connection failed', 'danger'); },
    });
  }

  // File viewers
  addFileViewer() {
    this.config.update(c => ({
      ...c,
      file_viewers: [...c.file_viewers, { extension: '.puml', label: 'PlantUML', renderer: 'plantuml', server_url: '' }],
    }));
    this.startEditing('viewer-' + (this.config().file_viewers.length - 1));
  }

  updateFileViewer(index: number, field: string, value: string) {
    this.config.update(c => {
      const viewers = [...c.file_viewers];
      viewers[index] = { ...viewers[index], [field]: value };
      return { ...c, file_viewers: viewers };
    });
  }

  removeFileViewer(index: number) {
    this.config.update(c => ({
      ...c,
      file_viewers: c.file_viewers.filter((_, i) => i !== index),
    }));
    this.editingSection.set(null);
    this.configBackup = null;
    this.persistConfig('Viewer removed');
  }

  // Scratch base dir
  updateScratchBaseDir(value: string) {
    this.config.update(c => ({ ...c, scratch_base_dir: value }));
  }

  saveScratchBaseDir() {
    this.persistConfig('Saved');
  }

  // Whisper test
  whisperTestText = signal('');

  // Secret visibility
  isSecretVisible(key: string): boolean { return this.visibleSecrets().has(key); }
  toggleSecret(key: string) {
    this.visibleSecrets.update(s => {
      const next = new Set(s);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
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

  // Import from folder
  startImport() {
    this.settingsService.browseFolder('').pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (res) => {
        this.importParentPath.set(res.path);
        this.settingsService.listSubfolders(res.path).subscribe({
          next: (result) => {
            const existingPaths = new Set(
              this.config().repositories.map((r) => r.path.replace(/\\/g, '/').replace(/\/+$/, '')),
            );
            const parentNorm = res.path.replace(/\\/g, '/').replace(/\/+$/, '');
            this.importFolders.set(
              result.folders.map((name) => ({
                name,
                selected: false,
                alreadyAdded: existingPaths.has(parentNorm + '/' + name),
              })),
            );
            this.importing.set(true);
            this.selectedRepoIndex.set(null);
          },
          error: (err) => this.showMessage(err.error?.detail || 'Failed to list subfolders', 'danger'),
        });
      },
    });
  }

  toggleImportFolder(index: number) {
    this.importFolders.update((folders) =>
      folders.map((f, i) => (i === index ? { ...f, selected: !f.selected } : f)),
    );
  }

  importSelectAll() {
    this.importFolders.update((folders) =>
      folders.map((f) => (f.alreadyAdded ? f : { ...f, selected: true })),
    );
  }

  importSelectNone() {
    this.importFolders.update((folders) =>
      folders.map((f) => ({ ...f, selected: false })),
    );
  }

  confirmImport() {
    const parent = this.importParentPath().replace(/\\/g, '/').replace(/\/+$/, '');
    const newRepos = this.importFolders()
      .filter((f) => f.selected && !f.alreadyAdded)
      .map((f) => ({
        name: f.name,
        path: parent + '/' + f.name,
        tags: [] as string[],
        modules: [] as ModuleConfig[],
        processors: [] as string[],
      }));
    if (newRepos.length === 0) {
      this.showMessage('No folders selected', 'danger');
      return;
    }
    this.config.update((c) => ({
      ...c,
      repositories: [...c.repositories, ...newRepos],
    }));
    this.importing.set(false);
    this.importFolders.set([]);
    this.persistConfig(`${newRepos.length} repositor${newRepos.length === 1 ? 'y' : 'ies'} added`);
  }

  cancelImport() {
    this.importing.set(false);
    this.importFolders.set([]);
    this.importParentPath.set('');
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
        const selectedName = this.selectedRepoIndex() !== null
          ? this.config().repositories[this.selectedRepoIndex()!]?.name
          : null;
        config.repositories.sort((a, b) => a.name.localeCompare(b.name));
        if (selectedName) {
          const newIdx = config.repositories.findIndex(r => r.name === selectedName);
          this.selectedRepoIndex.set(newIdx >= 0 ? newIdx : null);
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

export function itemKey(item: { type: string; id: string }): string {
  return `${item.type}::${item.id}`;
}

export function getItemIcon(type: string): string {
  switch (type) {
    case 'confluence_page': return 'bi-journal-text';
    case 'jira_issue': return 'bi-bug';
    case 'instructions': return 'bi-chat-left-text';
    case 'insight': return 'bi-lightbulb';
    case 'git_repo': return 'bi-git';
    case 'repo_file': return 'bi-file-code';
    case 'bitbucket_pr': return 'bi-git';
    case 'commits': return 'bi-git';
    case 'parent': return 'bi-box-arrow-in-up';
    case 'mixin': return 'bi-box-arrow-in-right';
    case 'inquiry': return 'bi-send';
    case 'scratch_dir': return 'bi-folder-symlink';
    case 'logzio': return 'bi-search';
    case 'url': return 'bi-link-45deg';
    default: return 'bi-file-text';
  }
}

export function getItemDisplayId(item: { type: string; id: string }): string {
  if (item.type === 'url' && item.id.length > 20) {
    return item.id.substring(0, 20) + '...';
  }
  return item.id;
}

export function getItemTypeLabel(type: string): string {
  switch (type) {
    case 'confluence_page': return 'Confluence';
    case 'jira_issue': return 'Jira';
    case 'instructions': return 'Instructions';
    case 'insight': return 'Agent Insight';
    case 'git_repo': return 'Git Repo';
    case 'repo_file': return 'File';
    case 'bitbucket_pr': return 'Bitbucket PR';
    case 'commits': return 'Commits';
    case 'parent': return 'Parent';
    case 'mixin': return 'Mixin';
    case 'inquiry': return 'Inquiry';
    case 'scratch_dir': return 'Scratch Dir';
    case 'logzio': return 'Logz.io';
    case 'url': return 'URL';
    default: return type;
  }
}

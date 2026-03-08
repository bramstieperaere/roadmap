export function itemKey(item: { type: string; id: string }): string {
  return `${item.type}::${item.id}`;
}

export function getItemIcon(type: string): string {
  switch (type) {
    case 'confluence_page': return 'bi-journal-text';
    case 'jira_issue': return 'bi-bug';
    case 'instructions': return 'bi-chat-left-text';
    case 'git_repo': return 'bi-git';
    case 'repo_file': return 'bi-file-code';
    case 'bitbucket_pr': return 'bi-git';
    case 'commits': return 'bi-git';
    case 'parent': return 'bi-box-arrow-in-up';
    case 'mixin': return 'bi-box-arrow-in-right';
    default: return 'bi-file-text';
  }
}

export function getItemTypeLabel(type: string): string {
  switch (type) {
    case 'confluence_page': return 'Confluence';
    case 'jira_issue': return 'Jira';
    case 'instructions': return 'Instructions';
    case 'git_repo': return 'Git Repo';
    case 'repo_file': return 'File';
    case 'bitbucket_pr': return 'Bitbucket PR';
    case 'commits': return 'Commits';
    case 'parent': return 'Parent';
    case 'mixin': return 'Mixin';
    default: return type;
  }
}

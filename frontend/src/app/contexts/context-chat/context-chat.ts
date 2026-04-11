import { Component, inject, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ContextAssistantService, ProposedAction } from '../../services/context-assistant';
import { getItemIcon, getItemTypeLabel } from '../context-utils';
import { WhisperInput } from '../../components/whisper-textarea/whisper-input';
import { AiTaskStatus } from '../../components/ai-task-status/ai-task-status';

export interface ChatAddItemEvent {
  type: string;
  id: string;
  label: string;
  text?: string;
}

export interface ChatTagEvent {
  action: 'add' | 'remove';
  tag: string;
}

export interface ChatDescriptionEvent {
  description: string;
}

interface ChatMessage {
  role: 'user' | 'assistant';
  text: string;
  proposals?: ProposedAction[];
}

@Component({
  selector: 'app-context-chat',
  standalone: true,
  imports: [FormsModule, WhisperInput, AiTaskStatus],
  templateUrl: './context-chat.html',
  styleUrl: './context-chat.scss',
})
export class ContextChat {
  private assistantService = inject(ContextAssistantService);

  contextName = input.required<string>();
  existingItems = input<{ type: string; id: string; label?: string }[]>([]);
  tags = input<string[]>([]);
  description = input('');

  addItem = output<ChatAddItemEvent>();
  tagAction = output<ChatTagEvent>();
  setDescription = output<ChatDescriptionEvent>();

  messages = signal<ChatMessage[]>([]);
  query = signal('');
  loading = signal(false);
  error = signal('');
  doneIds = signal<Set<string>>(new Set());  // confirmed or dismissed

  send() {
    const q = this.query().trim();
    if (!q || this.loading()) return;

    this.messages.update(m => [...m, { role: 'user', text: q }]);
    this.query.set('');
    this.loading.set(true);
    this.error.set('');

    this.assistantService.assist(q, this.contextName(), this.existingItems(), this.tags(), this.description()).subscribe({
      next: (res) => {
        this.loading.set(false);
        this.messages.update(m => [...m, {
          role: 'assistant',
          text: res.reply,
          proposals: res.proposals,
        }]);
      },
      error: (err) => {
        this.loading.set(false);
        this.error.set(err.error?.detail || 'Assistant failed');
      },
    });
  }

  confirm(p: ProposedAction) {
    if (p.action === 'add_item') {
      const event: ChatAddItemEvent = { type: p.type!, id: p.id!, label: p.label! };
      if (p.type === 'instructions') event.text = p.label;
      this.addItem.emit(event);
    } else if (p.action === 'add_tag') {
      this.tagAction.emit({ action: 'add', tag: p.tag! });
    } else if (p.action === 'remove_tag') {
      this.tagAction.emit({ action: 'remove', tag: p.tag! });
    } else if (p.action === 'set_description') {
      this.setDescription.emit({ description: p.description! });
    }
    this.doneIds.update(s => new Set(s).add(this.actionKey(p)));
  }

  dismiss(p: ProposedAction) {
    this.doneIds.update(s => new Set(s).add(this.actionKey(p)));
  }

  isDone(p: ProposedAction): boolean {
    return this.doneIds().has(this.actionKey(p));
  }

  isConfirmed(p: ProposedAction): boolean {
    return this.doneIds().has(this.actionKey(p));
  }

  actionKey(p: ProposedAction): string {
    if (p.action === 'add_item') return `item:${p.type}:${p.id}`;
    if (p.action === 'add_tag') return `tag+:${p.tag}`;
    if (p.action === 'remove_tag') return `tag-:${p.tag}`;
    if (p.action === 'set_description') return `desc:${p.description}`;
    return `${p.action}:${p.id}`;
  }

  typeLabel = getItemTypeLabel;

  actionIcon(p: ProposedAction): string {
    if (p.action === 'add_tag') return 'bi-tag';
    if (p.action === 'remove_tag') return 'bi-tag-fill';
    if (p.action === 'set_description') return 'bi-card-heading';
    return getItemIcon(p.type || '');
  }

  actionLabel(p: ProposedAction): string {
    if (p.action === 'add_tag') return `Add tag "${p.tag}"`;
    if (p.action === 'remove_tag') return `Remove tag "${p.tag}"`;
    if (p.action === 'set_description') return `Set description`;
    return p.label || p.id || '';
  }

  actionSublabel(p: ProposedAction): string {
    if (p.action === 'set_description') return p.description || '';
    if (p.action === 'add_item' && p.type) return `${p.type} — ${p.id}`;
    return '';
  }
}

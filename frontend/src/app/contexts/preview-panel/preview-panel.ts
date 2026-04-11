import { Component, computed, input, output, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { PreviewSection } from '../../services/contexts';
import { itemKey, getItemIcon, getItemTypeLabel } from '../context-utils';

@Component({
  selector: 'app-preview-panel',
  imports: [RouterLink],
  templateUrl: './preview-panel.html',
  styleUrl: './preview-panel.scss',
})
export class PreviewPanel {
  // Inputs
  sections = input<PreviewSection[]>([]);
  loading = input(false);
  selectedItemKeys = input<Set<string>>(new Set());
  primaryClickedKey = input<string | null>(null);
  contextName = input('');

  // Outputs
  refresh = output<void>();

  // Internal
  showDelimiters = signal(false);

  tocContent = computed(() => {
    const sections = this.sections();
    if (sections.length === 0) return '';
    const name = this.contextName();
    const n = sections.length;
    const lines: string[] = [
      `# Context: ${name}`,
      '',
      `This context contains ${n} item${n !== 1 ? 's' : ''}:`,
    ];
    for (let i = 0; i < n; i++) {
      lines.push(`${i + 1}. [${getItemTypeLabel(sections[i].type)}] ${sections[i].label}`);
    }
    lines.push('');
    lines.push(
      `Each item is delimited by ######### <item name> BEGIN ######### `
      + `and ######### <item name> END #########. `
      + `For example: ######### ${sections[0].label} BEGIN #########`
    );
    return lines.join('\n');
  });

  itemKey = itemKey;
  getItemIcon = getItemIcon;
  getItemTypeLabel = getItemTypeLabel;

  getSectionContent(section: PreviewSection): string {
    if (!this.showDelimiters()) return section.content;
    return `######### ${section.label} BEGIN #########\n\n${section.content}\n\n######### ${section.label} END #########`;
  }
}

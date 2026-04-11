import { Component, inject, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { FacetsService, FacetSummary, FacetDetail, FacetValueNode, ClassifiedNode } from '../services/facets';
import { ConfirmDialogService } from '../components/confirm-dialog/confirm-dialog.service';

interface FlatValue {
  name: string;
  label: string;
  ordinal: number;
  depth: number;
  childCount: number;
}

@Component({
  selector: 'app-facets',
  standalone: true,
  imports: [FormsModule, RouterLink],
  templateUrl: './facets.html',
  styleUrl: './facets.scss',
})
export class Facets implements OnInit {
  private svc = inject(FacetsService);
  private confirm = inject(ConfirmDialogService);

  // List
  facets = signal<FacetSummary[]>([]);
  loading = signal(true);
  error = signal('');

  // Detail
  selectedFacet = signal<FacetDetail | null>(null);
  flatValues = signal<FlatValue[]>([]);
  loadingDetail = signal(false);

  // Classified nodes for a selected value
  selectedValue = signal<string | null>(null);
  classifiedNodes = signal<ClassifiedNode[]>([]);
  loadingClassified = signal(false);

  // Create facet
  showCreate = signal(false);
  newName = signal('');
  newDesc = signal('');

  // Add value
  showAddForm = signal(false);
  addingToParent = signal(''); // empty = root, non-empty = parent value name
  newValueName = signal('');
  newValueLabel = signal('');

  ngOnInit() { this.loadFacets(); }

  private loadFacets() {
    this.loading.set(true);
    this.svc.getAll().subscribe({
      next: (f) => { this.facets.set(f); this.loading.set(false); },
      error: () => this.loading.set(false),
    });
  }

  // ── Create ──

  createFacet() {
    const name = this.newName().trim();
    if (!name) return;
    this.svc.create(name, this.newDesc().trim()).subscribe({
      next: () => {
        this.showCreate.set(false);
        this.newName.set('');
        this.newDesc.set('');
        this.loadFacets();
      },
      error: (err) => this.error.set(err.error?.detail || 'Failed to create facet'),
    });
  }

  // ── Select ──

  selectFacet(name: string) {
    this.loadingDetail.set(true);
    this.selectedValue.set(null);
    this.classifiedNodes.set([]);
    this.svc.get(name).subscribe({
      next: (f) => {
        this.selectedFacet.set(f);
        this.flatValues.set(this.flatten(f.values, 0));
        this.loadingDetail.set(false);
      },
      error: () => this.loadingDetail.set(false),
    });
  }

  back() {
    this.selectedFacet.set(null);
    this.flatValues.set([]);
    this.selectedValue.set(null);
    this.classifiedNodes.set([]);
    this.showAddForm.set(false);
    this.loadFacets();
  }

  // ── Delete facet ──

  async deleteFacet(name: string) {
    const ok = await this.confirm.open({
      title: 'Delete facet',
      message: `Delete "${name}" and all its values and classifications?`,
    });
    if (!ok) return;
    this.svc.remove(name).subscribe({
      next: () => {
        if (this.selectedFacet()?.name === name) this.back();
        else this.loadFacets();
      },
      error: (err) => this.error.set(err.error?.detail || 'Failed to delete facet'),
    });
  }

  // ── Add value ──

  startAddValue(parentName: string) {
    this.addingToParent.set(parentName);
    this.newValueName.set('');
    this.newValueLabel.set('');
    this.showAddForm.set(true);
  }

  cancelAddValue() { this.showAddForm.set(false); }

  addValue() {
    const facet = this.selectedFacet();
    if (!facet) return;
    const vname = this.newValueName().trim();
    if (!vname) return;
    const body = { name: vname, label: this.newValueLabel().trim() || vname };
    const parent = this.addingToParent();
    const obs = parent
      ? this.svc.addNarrower(facet.name, parent, body)
      : this.svc.addValue(facet.name, body);
    obs.subscribe({
      next: (f) => {
        this.selectedFacet.set(f);
        this.flatValues.set(this.flatten(f.values, 0));
        this.showAddForm.set(false);
      },
      error: (err) => this.error.set(err.error?.detail || 'Failed to add value'),
    });
  }

  // ── Delete value ──

  async deleteValue(valueName: string) {
    const facet = this.selectedFacet();
    if (!facet) return;
    const ok = await this.confirm.open({
      title: 'Delete value',
      message: `Delete "${valueName}" and all sub-values?`,
    });
    if (!ok) return;
    this.svc.removeValue(facet.name, valueName).subscribe({
      next: (f) => {
        this.selectedFacet.set(f);
        this.flatValues.set(this.flatten(f.values, 0));
        if (this.selectedValue() === valueName) {
          this.selectedValue.set(null);
          this.classifiedNodes.set([]);
        }
      },
      error: (err) => this.error.set(err.error?.detail || 'Failed to delete value'),
    });
  }

  // ── Show classified nodes ──

  showClassified(valueName: string) {
    const facet = this.selectedFacet();
    if (!facet) return;
    this.selectedValue.set(valueName);
    this.loadingClassified.set(true);
    this.svc.getClassifiedNodes(facet.name, valueName).subscribe({
      next: (nodes) => { this.classifiedNodes.set(nodes); this.loadingClassified.set(false); },
      error: () => this.loadingClassified.set(false),
    });
  }

  // ── Helpers ──

  private flatten(values: FacetValueNode[], depth: number): FlatValue[] {
    const result: FlatValue[] = [];
    for (const v of values) {
      result.push({
        name: v.name,
        label: v.label,
        ordinal: v.ordinal,
        depth,
        childCount: this.countDescendants(v),
      });
      result.push(...this.flatten(v.children, depth + 1));
    }
    return result;
  }

  private countDescendants(v: FacetValueNode): number {
    let count = v.children.length;
    for (const c of v.children) count += this.countDescendants(c);
    return count;
  }

  dismissError() { this.error.set(''); }

  labelDisplay(labels: string[]): string {
    return labels.filter(l => l !== 'Facet').join(':');
  }
}

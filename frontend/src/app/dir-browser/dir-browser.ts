import { Component, ElementRef, inject, OnInit, signal, viewChild } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { DomSanitizer, SafeHtml, SafeUrl } from '@angular/platform-browser';
import { marked } from 'marked';

interface DirEntry {
  name: string;
  path: string;
  type: 'dir' | 'file';
  size?: number;
  ext?: string;
}

interface DirListing {
  path: string;
  parent: string | null;
  entries: DirEntry[];
}

interface FileContent {
  path: string;
  name: string;
  ext: string;
  content: string;
  isImage?: boolean;
  imageUrl?: string;
}

const IMAGE_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.svg', '.ico']);

@Component({
  selector: 'app-dir-browser',
  standalone: true,
  imports: [RouterLink],
  templateUrl: './dir-browser.html',
  styleUrl: './dir-browser.scss',
})
export class DirBrowser implements OnInit {
  private route = inject(ActivatedRoute);
  private http = inject(HttpClient);
  private sanitizer = inject(DomSanitizer);

  currentPath = signal('');
  parentPath = signal<string | null>(null);
  returnTo = signal('');
  entries = signal<DirEntry[]>([]);
  loading = signal(true);
  error = signal('');

  // File viewer
  viewingFile = signal<FileContent | null>(null);
  loadingFile = signal(false);
  pumlSvgUrl = signal<SafeUrl | null>(null);
  pumlError = signal('');
  fileListCollapsed = signal(false);
  zoom = signal(100);
  showSource = signal(false);
  markdownHtml = signal<SafeHtml | null>(null);
  htmlBlobUrl = signal<SafeUrl | null>(null);
  private htmlBlobRef: string | null = null;

  // Minimap
  showMinimap = signal(false);
  minimapViewport = signal({ left: 0, top: 0, width: 100, height: 100 });
  scrollContainer = viewChild<ElementRef<HTMLElement>>('scrollContainer');
  private minimapDragging = false;

  ngOnInit() {
    this.route.queryParamMap.subscribe(params => {
      const path = params.get('path') || '';
      this.returnTo.set(params.get('returnTo') || '');
      this.currentPath.set(path);
      this.viewingFile.set(null);
      if (path) this.loadDir(path);
      else { this.loading.set(false); this.error.set('No path specified'); }
    });
  }

  private loadDir(path: string) {
    this.loading.set(true);
    this.error.set('');
    this.http.get<DirListing>('/api/browse-dir', { params: { path } }).subscribe({
      next: (res) => {
        this.entries.set(res.entries);
        this.parentPath.set(res.parent);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(err.error?.detail || 'Failed to load directory');
        this.loading.set(false);
      },
    });
  }

  openEntry(entry: DirEntry) {
    if (entry.type === 'dir') {
      this.viewingFile.set(null);
      this.currentPath.set(entry.path);
      this.loadDir(entry.path);
    } else {
      this.openFile(entry.path);
    }
  }

  goUp() {
    const parent = this.parentPath();
    if (parent) {
      this.viewingFile.set(null);
      this.currentPath.set(parent);
      this.loadDir(parent);
    }
  }

  private openFile(path: string) {
    this.loadingFile.set(true);
    this.pumlSvgUrl.set(null);

    const ext = path.substring(path.lastIndexOf('.')).toLowerCase();
    if (IMAGE_EXTENSIONS.has(ext)) {
      const name = path.replace(/\\/g, '/').split('/').pop() || path;
      const imageUrl = `/api/browse-dir/image?path=${encodeURIComponent(path)}`;
      this.viewingFile.set({ path, name, ext, content: '', isImage: true, imageUrl });
      this.loadingFile.set(false);
      return;
    }

    this.http.get<FileContent>('/api/browse-dir/read', { params: { path } }).subscribe({
      next: (res) => {
        this.viewingFile.set(res);
        this.loadingFile.set(false);
        if (res.ext === '.puml') {
          this.loadPumlUrl(path);
        }
        if (res.ext === '.md') {
          const html = marked.parse(res.content, { async: false }) as string;
          this.markdownHtml.set(this.sanitizer.bypassSecurityTrustHtml(html));
        } else {
          this.markdownHtml.set(null);
        }
        if (res.ext === '.html' || res.ext === '.htm') {
          this.loadHtmlPreview(res.content);
        } else {
          this.revokeHtmlBlob();
        }
      },
      error: (err) => { this.error.set(err.error?.detail || 'Failed to read file'); this.loadingFile.set(false); },
    });
  }

  private loadPumlUrl(path: string) {
    this.pumlError.set('');
    this.http.get<{ svg_url: string }>('/api/browse-dir/plantuml-url', { params: { path } }).subscribe({
      next: (res) => this.pumlSvgUrl.set(this.sanitizer.bypassSecurityTrustUrl(res.svg_url)),
      error: (err) => this.pumlError.set(err.error?.detail || 'PlantUML rendering not available'),
    });
  }

  private loadHtmlPreview(content: string) {
    this.revokeHtmlBlob();
    const blob = new Blob([content], { type: 'text/html' });
    this.htmlBlobRef = URL.createObjectURL(blob);
    this.htmlBlobUrl.set(
      this.sanitizer.bypassSecurityTrustResourceUrl(this.htmlBlobRef));
  }

  private revokeHtmlBlob() {
    if (this.htmlBlobRef) {
      URL.revokeObjectURL(this.htmlBlobRef);
      this.htmlBlobRef = null;
    }
    this.htmlBlobUrl.set(null);
  }

  closeFile() {
    this.viewingFile.set(null); this.pumlSvgUrl.set(null); this.pumlError.set('');
    this.zoom.set(100); this.showSource.set(false); this.showMinimap.set(false);
    this.markdownHtml.set(null); this.revokeHtmlBlob();
  }

  zoomIn() { this.zoom.update(z => Math.min(z + 25, 400)); this.updateMinimap(); }
  zoomOut() { this.zoom.update(z => Math.max(z - 25, 25)); this.updateMinimap(); }
  zoomFit() { this.zoom.set(100); this.updateMinimap(); }
  zoomReset() { this.zoom.set(100); this.updateMinimap(); }

  // ── Minimap ──

  toggleMinimap() {
    const next = !this.showMinimap();
    this.showMinimap.set(next);
    if (next) {
      requestAnimationFrame(() => this.updateMinimap());
    }
  }

  onScrollContainerReady() {
    if (this.showMinimap()) {
      requestAnimationFrame(() => this.updateMinimap());
    }
  }

  updateMinimap() {
    const el = this.scrollContainer()?.nativeElement;
    if (!el) return;
    requestAnimationFrame(() => {
      const sw = el.scrollWidth, sh = el.scrollHeight;
      if (sw === 0 || sh === 0) return;
      this.minimapViewport.set({
        left: (el.scrollLeft / sw) * 100,
        top: (el.scrollTop / sh) * 100,
        width: (el.clientWidth / sw) * 100,
        height: (el.clientHeight / sh) * 100,
      });
    });
  }

  onMinimapDown(event: MouseEvent) {
    event.preventDefault();
    this.minimapDragging = true;
    this.minimapNavigate(event);
    const onMove = (e: MouseEvent) => { if (this.minimapDragging) this.minimapNavigate(e); };
    const onUp = () => { this.minimapDragging = false; document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp); };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }

  private minimapNavigate(event: MouseEvent) {
    const el = this.scrollContainer()?.nativeElement;
    const minimap = (event.target as HTMLElement).closest('.minimap') as HTMLElement;
    if (!el || !minimap) return;
    const rect = minimap.getBoundingClientRect();
    const xRatio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    const yRatio = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
    const vp = this.minimapViewport();
    el.scrollLeft = (xRatio - vp.width / 200) * el.scrollWidth;
    el.scrollTop = (yRatio - vp.height / 200) * el.scrollHeight;
  }

  fileIcon(ext: string): string {
    switch (ext) {
      case '.md': return 'bi-markdown';
      case '.puml': return 'bi-diagram-2';
      case '.py': return 'bi-filetype-py';
      case '.json': return 'bi-filetype-json';
      case '.yaml': case '.yml': return 'bi-filetype-yml';
      case '.ts': case '.js': return 'bi-filetype-js';
      case '.html': return 'bi-filetype-html';
      case '.css': case '.scss': return 'bi-filetype-css';
      case '.xml': return 'bi-filetype-xml';
      case '.java': return 'bi-filetype-java';
      case '.png': case '.jpg': case '.jpeg': case '.gif':
      case '.bmp': case '.webp': case '.svg': case '.ico':
        return 'bi-file-earmark-image';
      default: return 'bi-file-earmark-text';
    }
  }

  formatSize(bytes?: number): string {
    if (!bytes) return '';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  dirName(): string {
    const p = this.currentPath();
    const parts = p.replace(/\\/g, '/').split('/');
    return parts[parts.length - 1] || p;
  }
}

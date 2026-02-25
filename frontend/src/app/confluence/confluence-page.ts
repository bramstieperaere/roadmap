import { Component, inject, OnInit, signal } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { DatePipe } from '@angular/common';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { ConfluenceService, ConfluencePage } from '../services/confluence';
import { FunctionalService } from '../services/functional';

@Component({
  selector: 'app-confluence-page',
  imports: [DatePipe],
  templateUrl: './confluence-page.html',
  styleUrl: './confluence-page.scss',
})
export class ConfluencePageComponent implements OnInit {
  private confluenceService = inject(ConfluenceService);
  private functionalService = inject(FunctionalService);
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private sanitizer = inject(DomSanitizer);

  page = signal<ConfluencePage | null>(null);
  loading = signal(false);
  error = signal('');
  bodyHtml = signal<SafeHtml>('');
  processing = signal(false);

  ngOnInit() {
    this.route.paramMap.subscribe(params => {
      const id = params.get('id');
      if (id) {
        this.fetchPage(id, false);
      } else {
        this.page.set(null);
        this.loading.set(false);
      }
    });
  }

  refresh() {
    const id = this.route.snapshot.paramMap.get('id');
    if (id) this.fetchPage(id, true);
  }

  private fetchPage(pageId: string, refresh: boolean) {
    this.loading.set(true);
    this.error.set('');
    this.confluenceService.getPage(pageId, refresh).subscribe({
      next: (data) => {
        this.page.set(data);
        this.bodyHtml.set(this.sanitizer.bypassSecurityTrustHtml(data.body_html));
        this.loading.set(false);
      },
      error: (err) => {
        this.loading.set(false);
        this.error.set(err.error?.detail || 'Failed to load page');
      },
    });
  }

  processPage() {
    const p = this.page();
    if (!p) return;
    this.processing.set(true);
    this.functionalService.processPages(p.space_key, [p.id]).subscribe({
      next: (res) => {
        this.processing.set(false);
        this.router.navigate(['/jobs', res.job_id]);
      },
      error: () => this.processing.set(false),
    });
  }

  goBack() {
    const spaceKey = this.route.snapshot.paramMap.get('spaceKey');
    this.router.navigate(spaceKey ? ['/confluence', spaceKey] : ['/confluence']);
  }

  navigateTo(pageId: string) {
    const spaceKey = this.route.snapshot.paramMap.get('spaceKey');
    this.router.navigate(spaceKey ? ['/confluence', spaceKey, 'page', pageId] : ['/confluence', 'page', pageId]);
  }
}

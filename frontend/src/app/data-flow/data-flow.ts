import { Component, inject, signal, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { DataFlowService, ServiceCard, ServiceDetail, EndpointItem, EndpointFlowDetail } from '../services/data-flow';
import { DataFlowDiagram } from './data-flow-diagram';
import { DataFlowEndpoint } from './data-flow-endpoint';

@Component({
  selector: 'app-data-flow',
  standalone: true,
  imports: [FormsModule, RouterLink, DataFlowDiagram, DataFlowEndpoint],
  templateUrl: './data-flow.html',
  styleUrl: './data-flow.scss',
})
export class DataFlow implements OnInit {
  private svc = inject(DataFlowService);

  services = signal<ServiceCard[]>([]);
  loading = signal(true);
  error = signal('');

  selectedName = signal<string | null>(null);
  selectedDetail = signal<ServiceDetail | null>(null);
  loadingDetail = signal(false);

  selectedEndpoint = signal<EndpointFlowDetail | null>(null);
  loadingEndpoint = signal(false);

  ngOnInit() {
    this.svc.getServices().subscribe({
      next: (data) => {
        this.services.set(data);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(err.error?.detail || 'Failed to load services');
        this.loading.set(false);
      },
    });
  }

  selectService(name: string) {
    this.selectedName.set(name);
    this.loadingDetail.set(true);
    this.svc.getServiceDetail(name).subscribe({
      next: (detail) => {
        this.selectedDetail.set(detail);
        this.loadingDetail.set(false);
      },
      error: (err) => {
        this.error.set(err.error?.detail || 'Failed to load service detail');
        this.loadingDetail.set(false);
      },
    });
  }

  selectEndpoint(ep: EndpointItem) {
    this.loadingEndpoint.set(true);
    this.svc.getEndpointFlow(this.selectedName()!, ep.path, ep.http_method).subscribe({
      next: (detail) => {
        this.selectedEndpoint.set(detail);
        this.loadingEndpoint.set(false);
      },
      error: (err) => {
        this.error.set(err.error?.detail || 'Failed to load endpoint flow');
        this.loadingEndpoint.set(false);
      },
    });
  }

  backToService() {
    this.selectedEndpoint.set(null);
    this.error.set('');
  }

  back() {
    this.selectedName.set(null);
    this.selectedDetail.set(null);
    this.selectedEndpoint.set(null);
    this.error.set('');
  }
}

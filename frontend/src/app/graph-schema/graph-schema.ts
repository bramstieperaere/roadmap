import { Component, AfterViewInit, OnDestroy, ElementRef, ViewChild, signal, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { QueryService } from '../services/query';
import { AiTaskStatus } from '../components/ai-task-status/ai-task-status';
import { WhisperInput } from '../components/whisper-textarea/whisper-input';

@Component({
  selector: 'app-graph-schema',
  imports: [RouterLink, AiTaskStatus, WhisperInput],
  templateUrl: './graph-schema.html',
  styleUrl: './graph-schema.scss',
})
export class GraphSchema implements AfterViewInit, OnDestroy {
  private queryService = inject(QueryService);

  copied = '';
  activeSection = signal('java');

  // Query generator
  question = signal('');
  generatedCypher = signal('');
  generating = signal(false);
  queryError = signal('');

  @ViewChild('scrollContent') private scrollContent!: ElementRef<HTMLElement>;
  private observer?: IntersectionObserver;

  ngAfterViewInit() {
    const root = this.scrollContent.nativeElement;
    this.observer = new IntersectionObserver(entries => {
      for (const e of entries) {
        if (e.isIntersecting) { this.activeSection.set(e.target.id); break; }
      }
    }, { root, rootMargin: '-10% 0px -55% 0px', threshold: 0 });
    root.querySelectorAll('section[id]').forEach(el => this.observer!.observe(el));
  }

  ngOnDestroy() { this.observer?.disconnect(); }

  scrollTo(id: string) {
    this.scrollContent.nativeElement
      .querySelector('#' + id)
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  readonly queries: Record<string, string> = {
    j1: `MATCH (:Java:Module {name: $module})
      -[:CONTAINS_PACKAGE]->(:Java:Package)
      -[:CONTAINS_CLASS]->(c:Java:Class)
RETURN c.full_name, c.kind
ORDER BY c.full_name`,

    j2: `MATCH (caller:Java:Method)-[:CALLS]->(m:Java:Method)
WHERE m.name = 'processOrder'
RETURN caller.full_name`,

    j3: `MATCH (p:Java:Package {name: 'service'})
      -[:CONTAINS_CLASS]->(c:Java:Class)
      -[r:HAS_METHOD]->(m:Java:Method)
RETURN p, c, r, m`,

    a1: `MATCH (ri:Arch:RESTInterface)-[:HAS_ENDPOINT]->(ep:Arch:RESTEndpoint)
      -[:IMPLEMENTED_BY]->(m:Java:Method)
      <-[:HAS_METHOD]-(c:Java:Class)
RETURN ri.name AS controller,
       ep.http_method + ' ' + ep.path AS endpoint,
       c.name AS class,
       m.name AS method
ORDER BY ri.name, ep.path`,

    a2: `MATCH (ms:Arch:Microservice)-[r1:IMPLEMENTED_BY]->(repo:Java:Repository)
      -[:CONTAINS_MODULE]->(:Java:Module)
      -[:CONTAINS_PACKAGE]->(:Java:Package)
      -[:CONTAINS_CLASS]->(c:Java:Class)
      <-[r2:IMPLEMENTED_BY]-(ri:Arch:RESTInterface)
RETURN ms, r1, repo, c, r2, ri`,

    a3: `MATCH (p:Arch:JMSProducer)-[r1:SENDS_TO]->(d:Arch:JMSDestination)
      <-[r2:LISTENS_ON]-(l:Arch:JMSListener)
RETURN p, r1, d, r2, l`,

    d1: `MATCH (ds:Data:Service)
OPTIONAL MATCH (ds)-[:EXPOSES]->(ie:Data:Endpoint)
OPTIONAL MATCH (ds)-[:CALLS]->(oe:Data:Endpoint)
OPTIONAL MATCH (ds)-[:PRODUCES|CONSUMES]->(q:Data:Queue)
OPTIONAL MATCH (ds)-[:READS_FROM|WRITES_TO]->(db:Data:Database)
RETURN ds.name AS service,
       count(DISTINCT ie) AS inbound,
       count(DISTINCT oe) AS outbound,
       count(DISTINCT q)  AS queues,
       count(DISTINCT db) AS databases
ORDER BY ds.name`,

    d2: `MATCH (ds:Data:Service {name: $service})
OPTIONAL MATCH (ds)-[r1]->(ep:Data:Endpoint)
OPTIONAL MATCH (ds)-[r2]->(q:Data:Queue)
OPTIONAL MATCH (ds)-[r3]->(db:Data:Database)
RETURN ds, r1, ep, r2, q, r3, db`,

    d3: `MATCH (producer:Data:Service)-[:PRODUCES]->(q:Data:Queue)
      <-[:CONSUMES]-(consumer:Data:Service)
RETURN producer.name, q.name, consumer.name`,

    t1: `MATCH (c:Tooling:Commit)
RETURN c.author_name AS author,
       count(c) AS commits
ORDER BY commits DESC`,

    t2: `MATCH (c:Tooling:Commit)-[p:PARENT {ord: 0}]->(parent:Tooling:Commit)
WHERE "main" IN c.branches
  AND "main" IN parent.branches
RETURN c, p, parent`,

    t3: `MATCH (c:Tooling:Commit)
WHERE 'PROJ-123' IN c.issue_keys
RETURN c.hash, c.date, c.author_name, c.message
ORDER BY c.date DESC`,

    t4: `MATCH (c:Tooling:Commit)
MATCH (:Java:Module {name: $module})
      -[:CONTAINS_PACKAGE]->(:Java:Package)
      -[:CONTAINS_CLASS]->(cls:Java:Class)
WHERE any(f IN c.files_changed WHERE f CONTAINS cls.name)
RETURN c.hash, c.date, c.author_name,
       c.message, c.files_changed
ORDER BY c.date DESC`,

    f1: `MATCH (f:Facet:Facet)-[hv:HAS_VALUE]->(v:Facet:Value)
RETURN f, hv, v`,

    f2: `MATCH (f:Facet:Facet {name: $facet})
      -[hv:HAS_VALUE]->(root:Facet:Value)
OPTIONAL MATCH (root)-[nr:NARROWER*]->(child:Facet:Value)
RETURN f, hv, root, nr, child`,

    f3: `MATCH (n)-[ca:CLASSIFIED_AS]->(v:Facet:Value {name: $value})
RETURN n, ca, v LIMIT 100`,
  };

  copy(id: string) {
    const text = this.queries[id] ?? '';
    navigator.clipboard.writeText(text).then(() => {
      this.copied = id;
      setTimeout(() => { if (this.copied === id) this.copied = ''; }, 1500);
    });
  }

  // ── Query generator ──

  generateCypher() {
    const q = this.question().trim();
    if (!q) return;
    this.generating.set(true);
    this.queryError.set('');
    this.generatedCypher.set('');
    this.queryService.executeQuery(q).subscribe({
      next: (res) => {
        this.generating.set(false);
        if (res.error) {
          this.queryError.set(res.error);
          this.generatedCypher.set(res.cypher);
        } else {
          this.generatedCypher.set(res.cypher);
        }
      },
      error: (err) => {
        this.generating.set(false);
        this.queryError.set(err.error?.detail || 'Failed to generate query');
      },
    });
  }

  copyCypher() {
    const cypher = this.generatedCypher();
    if (!cypher) return;
    navigator.clipboard.writeText(cypher).then(() => {
      this.copied = '_cypher';
      setTimeout(() => { if (this.copied === '_cypher') this.copied = ''; }, 1500);
    });
  }

  openInBrowser(id?: string) {
    const cypher = id ? this.queries[id] : this.generatedCypher();
    if (!cypher) return;
    const encoded = encodeURIComponent(cypher);
    window.open(`http://localhost:7474/browser/?cmd=edit&arg=${encoded}`, '_blank');
  }
}

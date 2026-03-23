from datetime import datetime, timezone


class NodeMeta:
    """Mixin that stamps job provenance on every Neo4j node.

    Classes that mix this in must set ``self.job_id`` and ``self.job_type``
    before calling ``node_meta()``.  The return value is a flat dict meant
    to be spread into a Cypher params dict::

        run_cypher_write(driver, "CREATE (n:Foo {name: $name, ...meta_props})",
                         {**self.node_meta(), "name": value})

    Properties written to every node:
      created_at  – ISO-8601 UTC timestamp of the Python call that created the node
      job_id      – short UUID of the job that created the node
      job_type    – pipeline phase: 'analysis' | 'enrichment' | 'data-flow' | …
    """

    job_id: str
    job_type: str

    def node_meta(self) -> dict:
        return {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "job_id": self.job_id,
            "job_type": self.job_type,
        }

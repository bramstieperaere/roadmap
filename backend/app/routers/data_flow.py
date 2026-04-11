from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.neo4j_client import get_neo4j_driver
from app.session import session

router = APIRouter(prefix="/api/data-flow", tags=["data-flow"])


def _require_unlocked():
    if not session.is_unlocked():
        raise HTTPException(status_code=403, detail="App is locked")


# ── Response models ──────────────────────────────────────────

class ServiceCard(BaseModel):
    name: str
    is_external: bool
    inbound_count: int
    outbound_count: int
    queue_count: int
    database_count: int


class EndpointItem(BaseModel):
    path: str
    http_method: str


class EndpointGroup(BaseModel):
    group_name: str
    base_path: str
    endpoints: list[EndpointItem]


class QueueItem(BaseModel):
    name: str
    type: str
    direction: str


class DatabaseItem(BaseModel):
    name: str
    technology: str
    access: list[str]


class ServiceDetail(BaseModel):
    name: str
    is_external: bool
    inbound_groups: list[EndpointGroup]
    outbound_groups: list[EndpointGroup]
    queues: list[QueueItem]
    databases: list[DatabaseItem]


class DataModelInfo(BaseModel):
    name: str
    full_name: str
    kind: str


class RepositoryInfo(BaseModel):
    name: str
    entity_type: str


class DatabaseWithRepos(BaseModel):
    name: str
    technology: str
    repositories: list[RepositoryInfo]


class EndpointFlowDetail(BaseModel):
    path: str
    http_method: str
    controller_name: str
    method_name: str
    request_models: list[DataModelInfo]
    response_models: list[DataModelInfo]
    outbound_groups: list[EndpointGroup]
    databases: list[DatabaseWithRepos]
    queues: list[QueueItem]


# ── Endpoints ────────────────────────────────────────────────

@router.get("/services", response_model=list[ServiceCard])
def list_services():
    _require_unlocked()
    driver = get_neo4j_driver()
    try:
        records, _, _ = driver.execute_query("""
            MATCH (ds:Data:Service)
            OPTIONAL MATCH (ds)-[:EXPOSES]->(ie:Data:Endpoint)
            OPTIONAL MATCH (ds)-[:CALLS]->(oe:Data:Endpoint)
            OPTIONAL MATCH (ds)-[:PRODUCES|CONSUMES]->(q:Data:Queue)
            OPTIONAL MATCH (ds)-[:READS_FROM|WRITES_TO]->(db:Data:Database)
            RETURN ds.name AS name,
                   coalesce(ds.is_external, false) AS is_external,
                   count(DISTINCT ie) AS inbound_count,
                   count(DISTINCT oe) AS outbound_count,
                   count(DISTINCT q) AS queue_count,
                   count(DISTINCT db) AS database_count
            ORDER BY ds.name
        """)
        return [ServiceCard(**dict(r)) for r in records]
    finally:
        pass


def _query_grouped_endpoints(driver, service_name: str,
                             rel_type: str,
                             parent_label: str) -> list[EndpointGroup]:
    """Query endpoints and group by their Arch parent (controller / client)."""
    arch_ep_label = "RESTEndpoint" if parent_label == "RESTInterface" else "FeignEndpoint"
    records, _, _ = driver.execute_query(f"""
        MATCH (ds:Data:Service {{name: $name}})-[:{rel_type}]->(de:Data:Endpoint)
        OPTIONAL MATCH (de)-[:MAPS_TO]->(ae:Arch:{arch_ep_label})
                       <-[:HAS_ENDPOINT]-(parent:Arch:{parent_label})
        RETURN de.path AS path,
               de.http_method AS http_method,
               coalesce(parent.name, 'Other') AS group_name,
               coalesce(parent.base_path,
                        parent.service_name,
                        parent.name, '') AS base_path
        ORDER BY group_name, de.path
    """, {"name": service_name})

    groups: dict[str, EndpointGroup] = {}
    for r in records:
        gn = r["group_name"]
        if gn not in groups:
            groups[gn] = EndpointGroup(
                group_name=gn, base_path=r["base_path"], endpoints=[])
        groups[gn].endpoints.append(
            EndpointItem(path=r["path"], http_method=r["http_method"]))
    return list(groups.values())


@router.get("/services/{name}", response_model=ServiceDetail)
def get_service_detail(name: str):
    _require_unlocked()
    driver = get_neo4j_driver()
    try:
        # Verify service exists
        records, _, _ = driver.execute_query("""
            MATCH (ds:Data:Service {name: $name})
            RETURN ds.name AS name,
                   coalesce(ds.is_external, false) AS is_external
        """, {"name": name})
        if not records:
            raise HTTPException(status_code=404, detail="Service not found")
        svc = dict(records[0])

        # Grouped inbound endpoints (by RESTInterface / controller)
        inbound_groups = _query_grouped_endpoints(
            driver, name, "EXPOSES", "RESTInterface")

        # Grouped outbound endpoints (by FeignClient)
        outbound_groups = _query_grouped_endpoints(
            driver, name, "CALLS", "FeignClient")

        # Queues
        records, _, _ = driver.execute_query("""
            MATCH (ds:Data:Service {name: $name})-[rel:PRODUCES|CONSUMES]->(q:Data:Queue)
            RETURN q.name AS name,
                   q.type AS type,
                   type(rel) AS direction
            ORDER BY q.name
        """, {"name": name})
        queues = [
            QueueItem(
                name=r["name"], type=r["type"],
                direction=r["direction"].lower(),
            )
            for r in records
        ]

        # Databases
        records, _, _ = driver.execute_query("""
            MATCH (ds:Data:Service {name: $name})-[rel:READS_FROM|WRITES_TO]->(db:Data:Database)
            RETURN db.name AS name,
                   db.technology AS technology,
                   collect(DISTINCT type(rel)) AS rels
            ORDER BY db.name
        """, {"name": name})
        databases = [
            DatabaseItem(
                name=r["name"], technology=r["technology"],
                access=[
                    a for rel in r["rels"]
                    for a in (["reads"] if rel == "READS_FROM" else ["writes"])
                ],
            )
            for r in records
        ]

        return ServiceDetail(
            name=svc["name"],
            is_external=svc["is_external"],
            inbound_groups=inbound_groups,
            outbound_groups=outbound_groups,
            queues=queues,
            databases=databases,
        )
    finally:
        pass


@router.get("/services/{name}/endpoint-flow",
            response_model=EndpointFlowDetail)
def get_endpoint_flow(name: str, path: str, method: str):
    """Level 3: detailed flow for a single inbound endpoint."""
    _require_unlocked()
    driver = get_neo4j_driver()
    try:
        # Endpoint + controller + method info
        records, _, _ = driver.execute_query("""
            MATCH (ds:Data:Service {name: $name})
                  -[:EXPOSES]->(de:Data:Endpoint
                      {path: $path, http_method: $method})
                  -[:MAPS_TO]->(ae:Arch:RESTEndpoint)
                  -[:IMPLEMENTED_BY]->(m:Java:Method)
            MATCH (c:Java:Class)-[:HAS_METHOD]->(m)
            OPTIONAL MATCH (ri:Arch:RESTInterface)
                           -[:HAS_ENDPOINT]->(ae)
            RETURN de.path AS path, de.http_method AS http_method,
                   coalesce(ri.name, c.name) AS controller_name,
                   m.name AS method_name
        """, {"name": name, "path": path, "method": method})
        if not records:
            raise HTTPException(status_code=404,
                                detail="Endpoint not found")
        ep = dict(records[0])

        # Request models (ACCEPTS)
        records, _, _ = driver.execute_query("""
            MATCH (ds:Data:Service {name: $name})
                  -[:EXPOSES]->(de:Data:Endpoint
                      {path: $path, http_method: $method})
                  -[:ACCEPTS]->(dm:Data:DataModel)
            RETURN dm.name AS name, dm.full_name AS full_name,
                   dm.kind AS kind
        """, {"name": name, "path": path, "method": method})
        request_models = [DataModelInfo(**dict(r)) for r in records]

        # Response models (RETURNS)
        records, _, _ = driver.execute_query("""
            MATCH (ds:Data:Service {name: $name})
                  -[:EXPOSES]->(de:Data:Endpoint
                      {path: $path, http_method: $method})
                  -[:RETURNS]->(dm:Data:DataModel)
            RETURN dm.name AS name, dm.full_name AS full_name,
                   dm.kind AS kind
        """, {"name": name, "path": path, "method": method})
        response_models = [DataModelInfo(**dict(r)) for r in records]

        # Outbound groups (service-level)
        outbound_groups = _query_grouped_endpoints(
            driver, name, "CALLS", "FeignClient")

        # Queues (service-level)
        records, _, _ = driver.execute_query("""
            MATCH (ds:Data:Service {name: $name})
                  -[rel:PRODUCES|CONSUMES]->(q:Data:Queue)
            RETURN q.name AS name, q.type AS type,
                   type(rel) AS direction
            ORDER BY q.name
        """, {"name": name})
        queues = [
            QueueItem(name=r["name"], type=r["type"],
                      direction=r["direction"].lower())
            for r in records
        ]

        # Databases with repository details
        records, _, _ = driver.execute_query("""
            MATCH (ds:Data:Service {name: $name})
                  -[:READS_FROM|WRITES_TO]->(db:Data:Database)
                  -[:MAPS_TO]->(ar:Arch:Repository)
            RETURN DISTINCT db.name AS db_name,
                   db.technology AS technology,
                   ar.name AS repo_name,
                   coalesce(ar.entity_type, '') AS entity_type
            ORDER BY db_name, repo_name
        """, {"name": name})
        db_map: dict[str, DatabaseWithRepos] = {}
        seen_repos: set[tuple[str, str]] = set()
        for r in records:
            db_name = r["db_name"]
            if db_name not in db_map:
                db_map[db_name] = DatabaseWithRepos(
                    name=db_name, technology=r["technology"],
                    repositories=[])
            key = (db_name, r["repo_name"])
            if key not in seen_repos:
                seen_repos.add(key)
                db_map[db_name].repositories.append(
                    RepositoryInfo(name=r["repo_name"],
                                   entity_type=r["entity_type"]))

        return EndpointFlowDetail(
            path=ep["path"],
            http_method=ep["http_method"],
            controller_name=ep["controller_name"],
            method_name=ep["method_name"],
            request_models=request_models,
            response_models=response_models,
            outbound_groups=outbound_groups,
            databases=list(db_map.values()),
            queues=queues,
        )
    finally:
        pass

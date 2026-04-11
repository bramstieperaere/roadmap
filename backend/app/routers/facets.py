from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import load_config_decrypted
from app.neo4j_client import get_neo4j_driver, run_cypher_write
from app.session import session

router = APIRouter(prefix="/api/facets", tags=["facets"])


# ── Pydantic models ──────────────────────────────────────────────────────────

class CreateFacetRequest(BaseModel):
    name: str
    description: str = ""


class UpdateFacetRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class CreateValueRequest(BaseModel):
    name: str
    label: str = ""
    ordinal: int = 0


class UpdateValueRequest(BaseModel):
    label: str | None = None
    ordinal: int | None = None


class ClassifyRequest(BaseModel):
    node_id: str
    facet_name: str
    value_name: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _require_unlocked():
    if not session.is_unlocked():
        raise HTTPException(status_code=403, detail="App is locked")


def _db():
    config = load_config_decrypted()
    return config.neo4j.database


def _build_tree(rows: list[dict]) -> list[dict]:
    """Build a tree from flat rows with id/parent_id."""
    by_id: dict[str, dict] = {}
    roots: list[dict] = []
    for r in rows:
        node = {
            "id": r["id"],
            "name": r["name"],
            "label": r["label"] or r["name"],
            "ordinal": r["ordinal"] or 0,
            "children": [],
        }
        by_id[r["id"]] = node
    for r in rows:
        node = by_id[r["id"]]
        pid = r.get("parent_id")
        if pid and pid in by_id:
            by_id[pid]["children"].append(node)
        else:
            roots.append(node)
    # Sort siblings by ordinal
    def sort_tree(nodes: list[dict]):
        nodes.sort(key=lambda n: n["ordinal"])
        for n in nodes:
            sort_tree(n["children"])
    sort_tree(roots)
    return roots


# ── Facet CRUD ────────────────────────────────────────────────────────────────

@router.get("")
def list_facets():
    _require_unlocked()
    driver = get_neo4j_driver()
    try:
        with driver.session(database=_db()) as s:
            result = s.run("""
                MATCH (f:Facet:Facet)
                OPTIONAL MATCH (f)-[:HAS_VALUE]->(root:Facet:Value)
                OPTIONAL MATCH (root)-[:NARROWER*0..]->(v:Facet:Value)
                RETURN f.name AS name, f.description AS description,
                       elementId(f) AS id, count(DISTINCT v) AS value_count
                ORDER BY f.name
            """)
            return [dict(r) for r in result]
    finally:
        pass


@router.post("")
def create_facet(req: CreateFacetRequest):
    _require_unlocked()
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    driver = get_neo4j_driver()
    try:
        with driver.session(database=_db()) as s:
            exists = s.run(
                "MATCH (f:Facet:Facet {name: $name}) RETURN f",
                {"name": name}).single()
            if exists:
                raise HTTPException(status_code=409,
                                    detail=f"Facet '{name}' already exists")
            s.run(
                "CREATE (f:Facet:Facet {name: $name, description: $desc})",
                {"name": name, "desc": req.description})
        return {"name": name, "description": req.description, "values": []}
    finally:
        pass


@router.get("/{name}")
def get_facet(name: str):
    _require_unlocked()
    driver = get_neo4j_driver()
    try:
        with driver.session(database=_db()) as s:
            facet = s.run(
                "MATCH (f:Facet:Facet {name: $name}) "
                "RETURN f.name AS name, f.description AS description, "
                "elementId(f) AS id",
                {"name": name}).single()
            if not facet:
                raise HTTPException(status_code=404,
                                    detail=f"Facet '{name}' not found")
            rows = s.run("""
                MATCH (f:Facet:Facet {name: $name})-[:HAS_VALUE]->(root:Facet:Value)
                OPTIONAL MATCH (root)-[:NARROWER*0..]->(v:Facet:Value)
                WITH v
                OPTIONAL MATCH (parent)-[:NARROWER]->(v)
                RETURN elementId(v) AS id, v.name AS name,
                       v.label AS label, v.ordinal AS ordinal,
                       elementId(parent) AS parent_id
            """, {"name": name})
            values = _build_tree([dict(r) for r in rows])
            return {
                "id": facet["id"],
                "name": facet["name"],
                "description": facet["description"] or "",
                "values": values,
            }
    finally:
        pass


@router.put("/{name}")
def update_facet(name: str, req: UpdateFacetRequest):
    _require_unlocked()
    driver = get_neo4j_driver()
    try:
        sets = []
        params: dict = {"name": name}
        if req.name is not None:
            sets.append("f.name = $new_name")
            params["new_name"] = req.name.strip()
        if req.description is not None:
            sets.append("f.description = $desc")
            params["desc"] = req.description
        if not sets:
            return get_facet(name)
        cypher = f"MATCH (f:Facet:Facet {{name: $name}}) SET {', '.join(sets)} RETURN f"
        with driver.session(database=_db()) as s:
            result = s.run(cypher, params).single()
            if not result:
                raise HTTPException(status_code=404,
                                    detail=f"Facet '{name}' not found")
        return get_facet(params.get("new_name", name))
    finally:
        pass


@router.delete("/{name}")
def delete_facet(name: str):
    _require_unlocked()
    driver = get_neo4j_driver()
    try:
        run_cypher_write(driver, """
            MATCH (f:Facet:Facet {name: $name})
            OPTIONAL MATCH (f)-[:HAS_VALUE]->(root:Facet:Value)
            OPTIONAL MATCH (root)-[:NARROWER*0..]->(v:Facet:Value)
            DETACH DELETE v, root, f
        """, {"name": name})
        return {"status": "deleted"}
    finally:
        pass


# ── Value CRUD ────────────────────────────────────────────────────────────────

@router.post("/{facet_name}/values")
def add_root_value(facet_name: str, req: CreateValueRequest):
    _require_unlocked()
    vname = req.name.strip()
    if not vname:
        raise HTTPException(status_code=400, detail="Name is required")
    driver = get_neo4j_driver()
    try:
        with driver.session(database=_db()) as s:
            facet = s.run(
                "MATCH (f:Facet:Facet {name: $fn}) RETURN f",
                {"fn": facet_name}).single()
            if not facet:
                raise HTTPException(status_code=404,
                                    detail=f"Facet '{facet_name}' not found")
            dup = s.run("""
                MATCH (f:Facet:Facet {name: $fn})-[:HAS_VALUE]->(root:Facet:Value)
                OPTIONAL MATCH (root)-[:NARROWER*0..]->(v:Facet:Value {name: $vn})
                RETURN v
            """, {"fn": facet_name, "vn": vname}).single()
            if dup and dup["v"]:
                raise HTTPException(status_code=409,
                                    detail=f"Value '{vname}' already exists in this facet")
            s.run("""
                MATCH (f:Facet:Facet {name: $fn})
                CREATE (f)-[:HAS_VALUE]->(v:Facet:Value {
                    name: $vn, label: $label, ordinal: $ordinal
                })
            """, {"fn": facet_name, "vn": vname,
                  "label": req.label or vname, "ordinal": req.ordinal})
        return get_facet(facet_name)
    finally:
        pass


@router.post("/{facet_name}/values/{parent_name}/narrower")
def add_narrower_value(facet_name: str, parent_name: str,
                       req: CreateValueRequest):
    _require_unlocked()
    vname = req.name.strip()
    if not vname:
        raise HTTPException(status_code=400, detail="Name is required")
    driver = get_neo4j_driver()
    try:
        with driver.session(database=_db()) as s:
            parent = s.run("""
                MATCH (f:Facet:Facet {name: $fn})-[:HAS_VALUE]->(root:Facet:Value)
                OPTIONAL MATCH (root)-[:NARROWER*0..]->(p:Facet:Value {name: $pn})
                RETURN p
            """, {"fn": facet_name, "pn": parent_name}).single()
            if not parent or not parent["p"]:
                raise HTTPException(status_code=404,
                                    detail=f"Parent value '{parent_name}' not found")
            # Check uniqueness within facet
            dup = s.run("""
                MATCH (f:Facet:Facet {name: $fn})-[:HAS_VALUE]->(root:Facet:Value)
                OPTIONAL MATCH (root)-[:NARROWER*0..]->(v:Facet:Value {name: $vn})
                RETURN v
            """, {"fn": facet_name, "vn": vname}).single()
            if dup and dup["v"]:
                raise HTTPException(status_code=409,
                                    detail=f"Value '{vname}' already exists in this facet")
            s.run("""
                MATCH (f:Facet:Facet {name: $fn})-[:HAS_VALUE]->(root:Facet:Value)
                WITH f, root
                MATCH (root)-[:NARROWER*0..]->(p:Facet:Value {name: $pn})
                CREATE (p)-[:NARROWER]->(v:Facet:Value {
                    name: $vn, label: $label, ordinal: $ordinal
                })
            """, {"fn": facet_name, "pn": parent_name,
                  "vn": vname, "label": req.label or vname,
                  "ordinal": req.ordinal})
        return get_facet(facet_name)
    finally:
        pass


@router.put("/{facet_name}/values/{value_name}")
def update_value(facet_name: str, value_name: str, req: UpdateValueRequest):
    _require_unlocked()
    driver = get_neo4j_driver()
    try:
        sets = []
        params: dict = {"fn": facet_name, "vn": value_name}
        if req.label is not None:
            sets.append("v.label = $label")
            params["label"] = req.label
        if req.ordinal is not None:
            sets.append("v.ordinal = $ordinal")
            params["ordinal"] = req.ordinal
        if not sets:
            return get_facet(facet_name)
        cypher = f"""
            MATCH (f:Facet:Facet {{name: $fn}})-[:HAS_VALUE]->(root:Facet:Value)
            MATCH (root)-[:NARROWER*0..]->(v:Facet:Value {{name: $vn}})
            SET {', '.join(sets)}
        """
        run_cypher_write(driver, cypher, params)
        return get_facet(facet_name)
    finally:
        pass


@router.delete("/{facet_name}/values/{value_name}")
def delete_value(facet_name: str, value_name: str):
    _require_unlocked()
    driver = get_neo4j_driver()
    try:
        run_cypher_write(driver, """
            MATCH (f:Facet:Facet {name: $fn})-[:HAS_VALUE]->(root:Facet:Value)
            MATCH (root)-[:NARROWER*0..]->(v:Facet:Value {name: $vn})
            OPTIONAL MATCH (v)-[:NARROWER*0..]->(child:Facet:Value)
            DETACH DELETE child, v
        """, {"fn": facet_name, "vn": value_name})
        return get_facet(facet_name)
    finally:
        pass


# ── Classification ────────────────────────────────────────────────────────────

@router.post("/classify")
def classify_node(req: ClassifyRequest):
    _require_unlocked()
    driver = get_neo4j_driver()
    try:
        run_cypher_write(driver, """
            MATCH (n) WHERE elementId(n) = $nid
            MATCH (f:Facet:Facet {name: $fn})-[:HAS_VALUE]->(root:Facet:Value)
            MATCH (root)-[:NARROWER*0..]->(v:Facet:Value {name: $vn})
            MERGE (n)-[:CLASSIFIED_AS]->(v)
        """, {"nid": req.node_id, "fn": req.facet_name,
              "vn": req.value_name})
        return {"status": "classified"}
    finally:
        pass


@router.delete("/classify")
def unclassify_node(req: ClassifyRequest):
    _require_unlocked()
    driver = get_neo4j_driver()
    try:
        run_cypher_write(driver, """
            MATCH (n)-[r:CLASSIFIED_AS]->(v:Facet:Value {name: $vn})
            WHERE elementId(n) = $nid
            DELETE r
        """, {"nid": req.node_id, "vn": req.value_name})
        return {"status": "unclassified"}
    finally:
        pass


@router.get("/classifications/{node_id:path}")
def get_classifications(node_id: str):
    _require_unlocked()
    driver = get_neo4j_driver()
    try:
        with driver.session(database=_db()) as s:
            result = s.run("""
                MATCH (n)-[:CLASSIFIED_AS]->(v:Facet:Value)
                WHERE elementId(n) = $nid
                MATCH (f:Facet:Facet)-[:HAS_VALUE]->(root:Facet:Value)
                WHERE (root)-[:NARROWER*0..]->(v) OR root = v
                RETURN f.name AS facet_name, v.name AS value_name,
                       v.label AS value_label
                ORDER BY f.name, v.name
            """, {"nid": node_id})
            return [dict(r) for r in result]
    finally:
        pass


@router.get("/{facet_name}/values/{value_name}/classified")
def get_classified_nodes(facet_name: str, value_name: str):
    _require_unlocked()
    driver = get_neo4j_driver()
    try:
        with driver.session(database=_db()) as s:
            result = s.run("""
                MATCH (f:Facet:Facet {name: $fn})-[:HAS_VALUE]->(root:Facet:Value)
                MATCH (root)-[:NARROWER*0..]->(v:Facet:Value {name: $vn})
                MATCH (n)-[:CLASSIFIED_AS]->(v)
                RETURN elementId(n) AS node_id, labels(n) AS labels,
                       coalesce(n.name, n.full_name, n.key, '') AS name
                ORDER BY name
                LIMIT 200
            """, {"fn": facet_name, "vn": value_name})
            return [dict(r) for r in result]
    finally:
        pass

from typing import Literal, Optional

from pydantic import BaseModel


class Neo4jConfig(BaseModel):
    uri: str = "bolt://localhost:7687"
    username: str = "neo4j"
    password: str = "roadmap"
    database: str = "neo4j"


class ModuleConfig(BaseModel):
    name: str
    type: Literal["java", "angular"]
    relative_path: str
    technologies: list[str] = []


class RepositoryConfig(BaseModel):
    name: str = ""
    path: str
    modules: list[ModuleConfig] = []


class JiraProjectConfig(BaseModel):
    key: str = ""
    name: str = ""
    board_id: int | None = None


class ConfluenceSpaceConfig(BaseModel):
    key: str = ""
    name: str = ""


class AtlassianConfig(BaseModel):
    deployment_type: Literal["cloud", "datacenter"] = "cloud"
    base_url: str = ""
    email: str = ""
    api_token: str = ""
    jira_projects: list[JiraProjectConfig] = []
    confluence_spaces: list[ConfluenceSpaceConfig] = []
    cache_dir: str = ""
    refresh_duration: int = 3600


class AIProviderConfig(BaseModel):
    name: str
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    default_model: str = "gpt-4o"


class AITaskConfig(BaseModel):
    task_type: str
    provider_name: str


class AppConfig(BaseModel):
    neo4j: Neo4jConfig = Neo4jConfig()
    atlassian: AtlassianConfig = AtlassianConfig()
    repositories: list[RepositoryConfig] = []
    ai_providers: list[AIProviderConfig] = []
    ai_tasks: list[AITaskConfig] = []
    encryption_salt: Optional[str] = None


class UnlockRequest(BaseModel):
    password: str


class LockStatusResponse(BaseModel):
    locked: bool
    has_encrypted_fields: bool


class AnalyzeRequest(BaseModel):
    repo_index: int


class AnalyzeResponse(BaseModel):
    modules: list[ModuleConfig]


class QueryRequest(BaseModel):
    question: str


class GraphNode(BaseModel):
    id: str
    labels: list[str]
    properties: dict


class GraphRelationship(BaseModel):
    id: str
    type: str
    start_node_id: str
    end_node_id: str
    properties: dict


class ExpandRequest(BaseModel):
    node_id: str
    operation: str
    depth: int = 3


class QueryResponse(BaseModel):
    cypher: str
    nodes: list[GraphNode]
    relationships: list[GraphRelationship]
    error: Optional[str] = None

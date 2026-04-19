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
    tags: list[str] = []
    modules: list[ModuleConfig] = []
    processors: list[str] = []


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
    bitbucket_username: str = ""
    bitbucket_app_password: str = ""
    jira_projects: list[JiraProjectConfig] = []
    confluence_spaces: list[ConfluenceSpaceConfig] = []
    cache_dir: str = ""
    refresh_duration: int = 3600


class AIProviderConfig(BaseModel):
    name: str
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    default_model: str = "gpt-4o"
    privacy_level: str = "private"


class AITaskConfig(BaseModel):
    task_type: str
    provider_name: str


class WhisperConfig(BaseModel):
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "whisper-1"
    postprocess_provider: str = ""
    postprocess_model: str = ""


class LogzioConfig(BaseModel):
    base_url: str = "https://api.logz.io"
    api_token: str = ""
    default_size: int = 50


class FileViewerConfig(BaseModel):
    extension: str = ""       # e.g. ".puml"
    label: str = ""           # e.g. "PlantUML"
    renderer: str = "text"    # "text" | "plantuml"
    server_url: str = ""      # e.g. "http://www.plantuml.com/plantuml"


class DatabaseOverride(BaseModel):
    repo_type: str       # "JPA", "Mongo", etc.
    name: str            # "MSSQL Orders DB"
    technology: str      # "mssql", "mongo"


class IncubatingProcessorConfig(BaseModel):
    name: str = ""
    label: str = ""
    description: str = ""
    instructions: str = ""
    file_patterns: list[str] = []
    instance_count: int = 0


class ProcessingProfileConfig(BaseModel):
    name: str = ""
    processors: list[str] = []


class GitProcessingConfig(BaseModel):
    name: str = ""
    repo_name: str = ""
    branch: str = ""
    profile: str = ""
    processors: list[str] = []


class SchedulingConfig(BaseModel):
    enabled: bool = False
    policy: str = "polling"          # only "polling" for now
    polling_schedule: str = ""       # cron expression, e.g. "0 9-17 * * 1-5"


class AppConfig(BaseModel):
    neo4j: Neo4jConfig = Neo4jConfig()
    atlassian: AtlassianConfig = AtlassianConfig()
    repositories: list[RepositoryConfig] = []
    ai_providers: list[AIProviderConfig] = []
    ai_tasks: list[AITaskConfig] = []
    whisper: WhisperConfig = WhisperConfig()
    logzio: LogzioConfig = LogzioConfig()
    scratch_base_dir: str = ""
    file_viewers: list[FileViewerConfig] = []
    encryption_salt: Optional[str] = None
    databases: list[DatabaseOverride] = []
    processing_profiles: list[ProcessingProfileConfig] = []
    git_processing: list[GitProcessingConfig] = []
    scheduling: SchedulingConfig = SchedulingConfig()
    incubating_processors: list[IncubatingProcessorConfig] = []


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

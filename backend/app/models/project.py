"""
Project context management.
Persists project state server-side so the frontend does not have to pass
large payloads between API calls.
"""

import os
import json
import uuid
import shutil
from datetime import datetime
from typing import Dict, Any, List, Optional
from enum import Enum
from dataclasses import dataclass, field
from ..config import Config


class ProjectStatus(str, Enum):
    """Project lifecycle status."""
    CREATED = "created"              # Just created, files uploaded
    ONTOLOGY_GENERATED = "ontology_generated"  # Ontology generated
    GRAPH_BUILDING = "graph_building"    # Graph build in progress
    GRAPH_COMPLETED = "graph_completed"  # Graph build finished
    FAILED = "failed"                # Failed


@dataclass
class Project:
    """Project data model."""
    project_id: str
    name: str
    status: ProjectStatus
    created_at: str
    updated_at: str
    
    # File information
    files: List[Dict[str, str]] = field(default_factory=list)  # [{filename, path, size}]
    total_text_length: int = 0
    
    # Ontology info (populated after endpoint 1 runs)
    ontology: Optional[Dict[str, Any]] = None
    analysis_summary: Optional[str] = None
    # The background task deriving the ontology. Recorded so a client that
    # reloads mid-generation can re-attach to it: the LLM call takes minutes,
    # and it used to be the request that also held the only copy of the new
    # project id.
    ontology_task_id: Optional[str] = None
    
    # Graph info (populated after endpoint 2 completes)
    graph_id: Optional[str] = None
    graph_build_task_id: Optional[str] = None
    zep_batch_id: Optional[str] = None
    zep_batch_operation_id: Optional[str] = None
    # When the document build finished, UTC ISO-8601. A running simulation
    # writes its agents' activity back into the same graph, so this is the
    # line between "the audience the brief describes" and everything the
    # simulation invented afterwards. See ZepEntityReader.filter_defined_entities.
    graph_built_at: Optional[str] = None
    
    # Audience priors harvested from public discussion during the graph build.
    # Absent when no source was configured or the harvest found nothing; the
    # rest of the pipeline treats that as "no priors", not as an error.
    corpus_distribution: Optional[Dict[str, Any]] = None
    corpus_summary: Optional[Dict[str, Any]] = None

    # Configuration
    simulation_requirement: Optional[str] = None
    chunk_size: int = 500
    chunk_overlap: int = 50
    
    # Error information
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a dict."""
        return {
            "project_id": self.project_id,
            "name": self.name,
            "status": self.status.value if isinstance(self.status, ProjectStatus) else self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "files": self.files,
            "total_text_length": self.total_text_length,
            "ontology": self.ontology,
            "analysis_summary": self.analysis_summary,
            "ontology_task_id": self.ontology_task_id,
            "graph_id": self.graph_id,
            "graph_build_task_id": self.graph_build_task_id,
            "zep_batch_id": self.zep_batch_id,
            "zep_batch_operation_id": self.zep_batch_operation_id,
            "graph_built_at": self.graph_built_at,
            "corpus_distribution": self.corpus_distribution,
            "corpus_summary": self.corpus_summary,
            "simulation_requirement": self.simulation_requirement,
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
            "error": self.error
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Project':
        """Build from a dict."""
        status = data.get('status', 'created')
        if isinstance(status, str):
            status = ProjectStatus(status)
        
        return cls(
            project_id=data['project_id'],
            name=data.get('name', 'Unnamed Project'),
            status=status,
            created_at=data.get('created_at', ''),
            updated_at=data.get('updated_at', ''),
            files=data.get('files', []),
            total_text_length=data.get('total_text_length', 0),
            ontology=data.get('ontology'),
            analysis_summary=data.get('analysis_summary'),
            ontology_task_id=data.get('ontology_task_id'),
            graph_id=data.get('graph_id'),
            graph_build_task_id=data.get('graph_build_task_id'),
            zep_batch_id=data.get('zep_batch_id'),
            zep_batch_operation_id=data.get('zep_batch_operation_id'),
            graph_built_at=data.get('graph_built_at'),
            corpus_distribution=data.get('corpus_distribution'),
            corpus_summary=data.get('corpus_summary'),
            simulation_requirement=data.get('simulation_requirement'),
            chunk_size=data.get('chunk_size', 500),
            chunk_overlap=data.get('chunk_overlap', 50),
            error=data.get('error')
        )


class ProjectManager:
    """Project manager - handles persistent storage and retrieval of projects."""
    
    # Root directory for project storage
    PROJECTS_DIR = os.path.join(Config.UPLOAD_FOLDER, 'projects')
    
    @classmethod
    def _ensure_projects_dir(cls):
        """Make sure the projects directory exists."""
        os.makedirs(cls.PROJECTS_DIR, exist_ok=True)
    
    @classmethod
    def _get_project_dir(cls, project_id: str) -> str:
        """Return the directory path for a project."""
        return os.path.join(cls.PROJECTS_DIR, project_id)
    
    @classmethod
    def _get_project_meta_path(cls, project_id: str) -> str:
        """Return the metadata file path for a project."""
        return os.path.join(cls._get_project_dir(project_id), 'project.json')
    
    @classmethod
    def _get_project_files_dir(cls, project_id: str) -> str:
        """Return the uploaded-files directory for a project."""
        return os.path.join(cls._get_project_dir(project_id), 'files')
    
    @classmethod
    def _get_project_text_path(cls, project_id: str) -> str:
        """Return the extracted-text file path for a project."""
        return os.path.join(cls._get_project_dir(project_id), 'extracted_text.txt')
    
    @classmethod
    def create_project(cls, name: str = "Unnamed Project") -> Project:
        """
        Create a new project.

        Args:
            name: Project name

        Returns:
            The newly created Project
        """
        cls._ensure_projects_dir()
        
        project_id = f"proj_{uuid.uuid4().hex[:12]}"
        now = datetime.now().isoformat()
        
        project = Project(
            project_id=project_id,
            name=name,
            status=ProjectStatus.CREATED,
            created_at=now,
            updated_at=now
        )
        
        # Create the project directory structure
        project_dir = cls._get_project_dir(project_id)
        files_dir = cls._get_project_files_dir(project_id)
        os.makedirs(project_dir, exist_ok=True)
        os.makedirs(files_dir, exist_ok=True)
        
        # Persist project metadata
        cls.save_project(project)
        
        return project
    
    @classmethod
    def save_project(cls, project: Project) -> None:
        """Persist project metadata."""
        project.updated_at = datetime.now().isoformat()
        meta_path = cls._get_project_meta_path(project.project_id)
        
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(project.to_dict(), f, ensure_ascii=False, indent=2)
    
    @classmethod
    def get_project(cls, project_id: str) -> Optional[Project]:
        """
        Load a project.

        Args:
            project_id: Project ID

        Returns:
            The Project, or None if it does not exist
        """
        meta_path = cls._get_project_meta_path(project_id)
        
        if not os.path.exists(meta_path):
            return None
        
        with open(meta_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        return Project.from_dict(data)
    
    @classmethod
    def list_projects(cls, limit: Optional[int] = 50) -> List[Project]:
        """
        List all projects.

        Args:
            limit: Maximum number of projects to return

        Returns:
            Projects, newest first
        """
        cls._ensure_projects_dir()
        
        projects = []
        for project_id in os.listdir(cls.PROJECTS_DIR):
            project = cls.get_project(project_id)
            if project:
                projects.append(project)
        
        # Sort by creation time, newest first
        projects.sort(key=lambda p: p.created_at, reverse=True)
        
        return projects if limit is None else projects[:limit]

    @classmethod
    def find_projects_by_graph_id(cls, graph_id: str) -> List[Project]:
        """Return every persisted project that references a Cloud graph."""

        return [
            project
            for project in cls.list_projects(limit=None)
            if project.graph_id == graph_id
        ]
    
    @classmethod
    def delete_project(cls, project_id: str) -> bool:
        """
        Delete a project and all of its files.

        Args:
            project_id: Project ID

        Returns:
            True if the project was deleted
        """
        project_dir = cls._get_project_dir(project_id)
        
        if not os.path.exists(project_dir):
            return False
        
        shutil.rmtree(project_dir)
        return True
    
    @classmethod
    def save_file_to_project(cls, project_id: str, file_storage, original_filename: str) -> Dict[str, str]:
        """
        Save an uploaded file into the project directory.

        Args:
            project_id: Project ID
            file_storage: Flask FileStorage object
            original_filename: Original file name

        Returns:
            File info dict {filename, path, size}
        """
        files_dir = cls._get_project_files_dir(project_id)
        os.makedirs(files_dir, exist_ok=True)
        
        # Generate a safe file name
        ext = os.path.splitext(original_filename)[1].lower()
        safe_filename = f"{uuid.uuid4().hex[:8]}{ext}"
        file_path = os.path.join(files_dir, safe_filename)
        
        # Save the file
        file_storage.save(file_path)
        
        # Read the file size
        file_size = os.path.getsize(file_path)
        
        return {
            "original_filename": original_filename,
            "saved_filename": safe_filename,
            "path": file_path,
            "size": file_size
        }
    
    @classmethod
    def save_extracted_text(cls, project_id: str, text: str) -> None:
        """Persist the extracted text."""
        text_path = cls._get_project_text_path(project_id)
        with open(text_path, 'w', encoding='utf-8') as f:
            f.write(text)
    
    @classmethod
    def get_extracted_text(cls, project_id: str) -> Optional[str]:
        """Load the extracted text."""
        text_path = cls._get_project_text_path(project_id)
        
        if not os.path.exists(text_path):
            return None
        
        with open(text_path, 'r', encoding='utf-8') as f:
            return f.read()
    

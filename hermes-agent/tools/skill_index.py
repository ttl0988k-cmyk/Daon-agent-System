#!/usr/bin/env python3
"""
Skills Index Module

Centralized skill indexing system for fast skill discovery.
Instead of scanning the filesystem every time (rglob + parse frontmatter),
we maintain a skills.json index file that is updated on changes.

Index file format:
{
  "version": "1.0.0",
  "updated_at": "ISO timestamp",
  "skills": [
    {
      "id": "ui-ux-pro-max",
      "category": "design",
      "name": "UI UX Pro Max",
      "version": "1.0.0",
      "tags": ["ui", "ux", "figma"],
      "enabled": true,
      "path": "design/ui-ux-pro-max",
      "source": "cursor-rules",
      "description": "...",
      "readiness_status": "available"
    }
  ]
}

Usage:
    from tools.skill_index import SkillIndex, get_skill_index
    
    # Get singleton index instance
    index = get_skill_index()
    
    # List all skills (from index, not filesystem)
    skills = index.list_skills()
    
    # Get single skill info
    skill = index.get_skill("ui-ux-pro-max")
    
    # Rebuild index from filesystem
    index.rebuild()
    
    # Update single skill in index
    index.update_skill("ui-ux-pro-max", {...})
"""

import json
import logging
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

# Index file location
INDEX_FILE_NAME = "skills-index.json"

# Index TTL in seconds (refresh from filesystem after this)
INDEX_TTL_SECONDS = 300  # 5 minutes

# Excluded directories during scan
EXCLUDED_DIRS = frozenset((".git", ".github", ".hub", ".bundled_manifest"))


class SkillReadinessStatus(str, Enum):
    AVAILABLE = "available"
    SETUP_NEEDED = "setup_needed"
    UNSUPPORTED = "unsupported"
    DISABLED = "disabled"


@dataclass
class SkillEntry:
    """Single skill entry in the index."""
    id: str
    category: str
    name: str
    version: str = "1.0.0"
    tags: List[str] = field(default_factory=list)
    enabled: bool = True
    path: str = ""  # Relative path from skills dir
    source: str = "local"  # "local", "cursor-rules", "github", etc.
    source_url: str = ""
    description: str = ""
    readiness_status: str = SkillReadinessStatus.AVAILABLE.value
    updated_at: str = ""


@dataclass
class SkillIndexData:
    """Root structure of the index file."""
    version: str = "1.0.0"
    updated_at: str = ""
    skills: List[SkillEntry] = field(default_factory=list)


class SkillIndex:
    """
    Centralized skill index manager.
    
    Maintains a skills-index.json file that provides fast skill discovery
    without needing to scan the filesystem each time.
    
    The index is refreshed:
    - On first access if older than INDEX_TTL_SECONDS
    - When explicitly rebuilt via rebuild()
    - When a skill is updated/created/deleted via update_skill/delete_skill
    """
    
    _instance: Optional["SkillIndex"] = None
    _lock = threading.Lock()
    
    def __init__(self, skills_dir: Optional[Path] = None, index_path: Optional[Path] = None):
        """
        Initialize skill index.
        
        Args:
            skills_dir: Path to skills directory (default: ~/.hermes/skills/)
            index_path: Path to index file (default: ~/.hermes/skills-index.json)
        """
        self._hermes_home = get_hermes_home()
        self._skills_dir = skills_dir or (self._hermes_home / "skills")
        self._index_path = index_path or (self._hermes_home / "skills-index.json")
        
        # In-memory cache
        self._index_data: Optional[SkillIndexData] = None
        self._last_refresh: float = 0
        self._index_mtime: float = 0
        
        # Track dirty state (needs write)
        self._dirty: bool = False
    
    @classmethod
    def get_instance(cls) -> "SkillIndex":
        """Get singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance
    
    def _ensure_index_loaded(self) -> SkillIndexData:
        """Load index from disk if needed."""
        current_time = time.time()
        
        # Check if we need to reload
        need_reload = False
        
        if self._index_data is None:
            need_reload = True
            logger.debug("Index not loaded, loading...")
        elif current_time - self._last_refresh > INDEX_TTL_SECONDS:
            need_reload = True
            logger.debug("Index TTL expired, reloading...")
        else:
            # Check if index file was modified externally
            try:
                mtime = self._index_path.stat().st_mtime
                if mtime != self._index_mtime:
                    need_reload = True
                    logger.debug("Index file modified externally, reloading...")
            except FileNotFoundError:
                need_reload = True
                logger.debug("Index file not found, building...")
        
        if need_reload:
            self._load_index()
            self._last_refresh = current_time
        
        return self._index_data or SkillIndexData()
    
    def _load_index(self) -> None:
        """Load index from disk."""
        try:
            if self._index_path.exists():
                with open(self._index_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                self._index_mtime = self._index_path.stat().st_mtime
                
                # Parse into SkillIndexData
                skills = []
                for s in data.get("skills", []):
                    entry = SkillEntry(
                        id=s.get("id", ""),
                        category=s.get("category", ""),
                        name=s.get("name", ""),
                        version=s.get("version", "1.0.0"),
                        tags=s.get("tags", []),
                        enabled=s.get("enabled", True),
                        path=s.get("path", ""),
                        source=s.get("source", "local"),
                        source_url=s.get("source_url", ""),
                        description=s.get("description", ""),
                        readiness_status=s.get("readiness_status", SkillReadinessStatus.AVAILABLE.value),
                        updated_at=s.get("updated_at", ""),
                    )
                    skills.append(entry)
                
                self._index_data = SkillIndexData(
                    version=data.get("version", "1.0.0"),
                    updated_at=data.get("updated_at", ""),
                    skills=skills,
                )
                logger.info(f"Loaded skill index with {len(skills)} entries")
            else:
                self._index_data = SkillIndexData()
                logger.info("No existing index file, starting fresh")
        except Exception as e:
            logger.warning(f"Failed to load index: {e}, starting fresh")
            self._index_data = SkillIndexData()
    
    def _save_index(self) -> None:
        """Save index to disk."""
        if self._index_data is None:
            return
        
        try:
            # Ensure parent directory exists
            self._index_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Serialize
            data = {
                "version": self._index_data.version,
                "updated_at": self._index_data.updated_at,
                "skills": [
                    {
                        "id": s.id,
                        "category": s.category,
                        "name": s.name,
                        "version": s.version,
                        "tags": s.tags,
                        "enabled": s.enabled,
                        "path": s.path,
                        "source": s.source,
                        "source_url": s.source_url,
                        "description": s.description,
                        "readiness_status": s.readiness_status,
                        "updated_at": s.updated_at,
                    }
                    for s in self._index_data.skills
                ],
            }
            
            # Atomic write
            temp_path = self._index_path.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            temp_path.replace(self._index_path)
            
            self._index_mtime = self._index_path.stat().st_mtime
            self._dirty = False
            logger.debug("Index saved successfully")
        except Exception as e:
            logger.error(f"Failed to save index: {e}")
            raise
    
    def list_skills(
        self,
        category: Optional[str] = None,
        enabled_only: bool = True,
        include_disabled: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        List all skills from the index.
        
        Args:
            category: Optional category filter
            enabled_only: If True, only return enabled skills
            include_disabled: If True, include disabled skills in count
            
        Returns:
            List of skill info dicts
        """
        index_data = self._ensure_index_loaded()
        
        results = []
        for skill in index_data.skills:
            # Category filter
            if category and skill.category != category:
                continue
            
            # Enabled filter
            if enabled_only and not skill.enabled:
                continue
            
            results.append({
                "id": skill.id,
                "name": skill.name,
                "category": skill.category,
                "description": skill.description,
                "version": skill.version,
                "tags": skill.tags,
                "enabled": skill.enabled,
                "path": skill.path,
                "source": skill.source,
                "readiness_status": skill.readiness_status,
            })
        
        return results
    
    def get_skill(self, skill_id: str) -> Optional[Dict[str, Any]]:
        """
        Get single skill info from index.
        
        Args:
            skill_id: Skill ID (e.g., "ui-ux-pro-max")
            
        Returns:
            Skill info dict or None if not found
        """
        index_data = self._ensure_index_loaded()
        
        for skill in index_data.skills:
            if skill.id == skill_id:
                return {
                    "id": skill.id,
                    "name": skill.name,
                    "category": skill.category,
                    "description": skill.description,
                    "version": skill.version,
                    "tags": skill.tags,
                    "enabled": skill.enabled,
                    "path": skill.path,
                    "source": skill.source,
                    "source_url": skill.source_url,
                    "readiness_status": skill.readiness_status,
                    "updated_at": skill.updated_at,
                }
        
        return None
    
    def get_skill_path(self, skill_id: str) -> Optional[Path]:
        """
        Get filesystem path for a skill.
        
        Args:
            skill_id: Skill ID
            
        Returns:
            Path to skill directory or None
        """
        skill = self.get_skill(skill_id)
        if skill and skill.get("path"):
            return self._skills_dir / skill["path"]
        return None
    
    def skill_exists(self, skill_id: str) -> bool:
        """Check if skill exists in index."""
        return self.get_skill(skill_id) is not None
    
    def rebuild(self, force: bool = False) -> int:
        """
        Rebuild index from filesystem.
        
        Args:
            force: If True, ignore TTL and rebuild even if recent
            
        Returns:
            Number of skills indexed
        """
        if not force:
            current_time = time.time()
            if current_time - self._last_refresh < INDEX_TTL_SECONDS:
                logger.debug("Index recently refreshed, skipping rebuild")
                return len(self._index_data.skills) if self._index_data else 0
        
        logger.info("Rebuilding skill index from filesystem...")
        
        skills: List[SkillEntry] = []
        seen_ids: Set[str] = set()
        
        if not self._skills_dir.exists():
            logger.warning(f"Skills directory does not exist: {self._skills_dir}")
            self._index_data = SkillIndexData(
                version="1.0.0",
                updated_at=datetime.now(timezone.utc).isoformat(),
                skills=[],
            )
            self._save_index()
            return 0
        
        # Scan for SKILL.md files
        for skill_md in self._skills_dir.rglob("SKILL.md"):
            # Skip excluded directories
            if any(excluded in skill_md.parts for excluded in EXCLUDED_DIRS):
                continue
            
            skill_dir = skill_md.parent
            
            try:
                # Read frontmatter
                content = skill_md.read_text(encoding="utf-8")
                frontmatter, body = self._parse_frontmatter(content)
                
                # Extract metadata
                skill_id = frontmatter.get("name", skill_dir.name)
                description = frontmatter.get("description", "")
                
                # Get description from body if not in frontmatter
                if not description:
                    for line in body.strip().split("\n"):
                        line = line.strip()
                        if line and not line.startswith("#"):
                            description = line[:200]  # Truncate
                            break
                
                # Determine category from path
                rel_path = skill_md.relative_to(self._skills_dir).parent
                category = str(rel_path).split("/")[0] if str(rel_path) != "." else "uncategorized"
                
                # Get tags
                tags = frontmatter.get("tags", [])
                if isinstance(tags, str):
                    tags = [t.strip() for t in tags.split(",") if t.strip()]
                
                # Get version
                version = frontmatter.get("version", "1.0.0")
                
                # Get source
                source = frontmatter.get("source", "local")
                
                # Skip duplicates
                if skill_id in seen_ids:
                    continue
                seen_ids.add(skill_id)
                
                # Create entry
                entry = SkillEntry(
                    id=skill_id,
                    category=category,
                    name=frontmatter.get("name", skill_id),
                    version=version,
                    tags=tags,
                    enabled=True,
                    path=str(rel_path) if str(rel_path) != "." else skill_id,
                    source=source,
                    source_url=frontmatter.get("source_url", ""),
                    description=description[:500] if description else "",
                    readiness_status=SkillReadinessStatus.AVAILABLE.value,
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )
                skills.append(entry)
                
            except Exception as e:
                logger.debug(f"Failed to parse skill {skill_md}: {e}")
                continue
        
        # Sort by category then name
        skills.sort(key=lambda s: (s.category or "", s.name))
        
        # Update index data
        self._index_data = SkillIndexData(
            version="1.0.0",
            updated_at=datetime.now(timezone.utc).isoformat(),
            skills=skills,
        )
        self._last_refresh = time.time()
        
        # Save to disk
        self._save_index()
        
        logger.info(f"Index rebuilt: {len(skills)} skills indexed")
        return len(skills)
    
    def update_skill(self, skill_id: str, updates: Dict[str, Any]) -> bool:
        """
        Update a single skill in the index.
        
        Args:
            skill_id: Skill ID to update
            updates: Dict of fields to update
            
        Returns:
            True if skill was found and updated
        """
        index_data = self._ensure_index_loaded()
        
        for skill in index_data.skills:
            if skill.id == skill_id:
                # Apply updates
                for key, value in updates.items():
                    if hasattr(skill, key):
                        setattr(skill, key, value)
                
                skill.updated_at = datetime.now(timezone.utc).isoformat()
                self._dirty = True
                self._save_index()
                logger.debug(f"Updated skill in index: {skill_id}")
                return True
        
        return False
    
    def add_skill(self, skill: SkillEntry) -> None:
        """
        Add a new skill to the index.
        
        Args:
            skill: SkillEntry to add
        """
        index_data = self._ensure_index_loaded()
        
        # Check for duplicate
        if any(s.id == skill.id for s in index_data.skills):
            logger.warning(f"Skill already exists in index: {skill.id}")
            return
        
        index_data.skills.append(skill)
        index_data.updated_at = datetime.now(timezone.utc).isoformat()
        self._dirty = True
        self._save_index()
        logger.debug(f"Added skill to index: {skill.id}")
    
    def remove_skill(self, skill_id: str) -> bool:
        """
        Remove a skill from the index.
        
        Args:
            skill_id: Skill ID to remove
            
        Returns:
            True if skill was found and removed
        """
        index_data = self._ensure_index_loaded()
        
        original_len = len(index_data.skills)
        index_data.skills = [s for s in index_data.skills if s.id != skill_id]
        
        if len(index_data.skills) < original_len:
            index_data.updated_at = datetime.now(timezone.utc).isoformat()
            self._dirty = True
            self._save_index()
            logger.debug(f"Removed skill from index: {skill_id}")
            return True
        
        return False
    
    def get_categories(self) -> List[str]:
        """Get list of all categories."""
        index_data = self._ensure_index_loaded()
        categories = sorted(set(s.category for s in index_data.skills if s.category))
        return categories
    
    def search_skills(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Search skills by name, description, or tags.
        
        Args:
            query: Search query string
            limit: Maximum results to return
            
        Returns:
            List of matching skill dicts
        """
        index_data = self._ensure_index_loaded()
        query_lower = query.lower()
        
        results = []
        for skill in index_data.skills:
            # Score based on match quality
            score = 0
            
            # Name match (highest weight)
            if query_lower in skill.name.lower():
                score += 100
            if query_lower in skill.id.lower():
                score += 50
            
            # Description match
            if query_lower in skill.description.lower():
                score += 20
            
            # Tags match
            for tag in skill.tags:
                if query_lower in tag.lower():
                    score += 10
            
            if score > 0:
                result = {
                    "id": skill.id,
                    "name": skill.name,
                    "category": skill.category,
                    "description": skill.description[:100],
                    "tags": skill.tags,
                    "score": score,
                }
                results.append(result)
        
        # Sort by score descending
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]
    
    def _parse_frontmatter(self, content: str) -> tuple:
        """
        Parse YAML frontmatter from markdown content.
        
        Args:
            content: Full file content
            
        Returns:
            (frontmatter_dict, body_string)
        """
        import yaml
        
        frontmatter = {}
        body = content
        
        if not content.startswith("---"):
            return frontmatter, body
        
        # Find end of frontmatter
        parts = content[3:].split("\n---\n", 1)
        if len(parts) != 2:
            return frontmatter, body
        
        yaml_content = parts[0]
        body = parts[1]
        
        try:
            parsed = yaml.safe_load(yaml_content)
            if isinstance(parsed, dict):
                frontmatter = parsed
        except Exception:
            # Fallback: simple key:value parsing
            for line in yaml_content.strip().split("\n"):
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                frontmatter[key.strip()] = value.strip()
        
        return frontmatter, body
    
    def invalidate(self) -> None:
        """Invalidate cached index, force reload on next access."""
        self._index_data = None
        self._last_refresh = 0
        logger.debug("Index invalidated")


# Singleton accessor
def get_skill_index() -> SkillIndex:
    """Get the singleton SkillIndex instance."""
    return SkillIndex.get_instance()


# Module-level convenience functions
def list_skills(category: str = None, enabled_only: bool = True) -> List[Dict[str, Any]]:
    """List all skills from index."""
    return get_skill_index().list_skills(category=category, enabled_only=enabled_only)


def get_skill(skill_id: str) -> Optional[Dict[str, Any]]:
    """Get single skill info from index."""
    return get_skill_index().get_skill(skill_id)


def get_skill_path(skill_id: str) -> Optional[Path]:
    """Get filesystem path for a skill."""
    return get_skill_index().get_skill_path(skill_id)


def rebuild_index(force: bool = False) -> int:
    """Rebuild the skill index from filesystem."""
    return get_skill_index().rebuild(force=force)


def search_skills(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Search skills by query."""
    return get_skill_index().search_skills(query, limit=limit)

"""Crayotter story-development workflow.

This package is intentionally separate from the video-editing graph.  It
shares Crayotter's model gateway, worker protocol, project sandbox and artifact
registry, while keeping screenplay-specific state and contracts isolated.
"""

from .models import StoryDocument, StoryJobConfig

__all__ = ["StoryDocument", "StoryJobConfig"]

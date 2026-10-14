from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from schrift.config import Settings
from schrift.db import Database
from schrift.models import User
from schrift.services.vault import Vault


@dataclass
class AppContext:
    """Process-wide state shared by the web app, CalDAV and MCP."""

    settings: Settings
    db: Database
    semantic: object | None = None  # schrift.services.semantic.SemanticIndex
    extras: dict = field(default_factory=dict)

    def vault(self, session: Session, user: User) -> Vault:
        on_change = self.semantic.schedule if self.semantic else None
        return Vault(session, user, self.settings.data_dir, on_change=on_change)

"""Resolve workspace paths without creating, moving or probing user files."""
import os
from dataclasses import dataclass
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

@dataclass(frozen=True)
class RuntimePaths:
    repository: Path
    data: Path
    backups: Path

    @classmethod
    def from_env(cls,root=ROOT,data=None):
        root=Path(root).resolve()
        def resolve(value):
            if not str(value).strip():raise ValueError('工作空间路径不能为空')
            return Path(value).expanduser().resolve()
        return cls(root,resolve(data if data is not None else os.getenv('MEETING_DATA',str(root/'data'))),
                   resolve(os.getenv('MEETING_BACKUPS',str(root/'backups'))))

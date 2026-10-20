"""Bounded formation settings; thresholds are engineering defaults, not quality claims."""
from dataclasses import asdict, dataclass, fields


@dataclass(frozen=True)
class FormationSettings:
    enabled: bool = True
    min_sources: int = 3
    min_sessions: int = 2
    max_observations: int = 24
    retention_days: int = 90
    core_half_life_days: int = 45
    core_chars: int = 1200
    document_min_sources: int = 3

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or set(data) - {f.name for f in fields(cls)}:
            raise ValueError('Invalid memory.formation settings')
        limits = {'min_sources': (3, 16), 'min_sessions': (2, 16),
                  'max_observations': (3, 32), 'retention_days': (1, 3650),
                  'core_half_life_days': (1, 3650), 'core_chars': (0, 4000), 'document_min_sources': (3,8)}
        for key, value in data.items():
            if key == 'enabled':
                if type(value) is not bool:
                    raise ValueError('memory.formation.enabled must be boolean')
            elif type(value) is not int or not limits[key][0] <= value <= limits[key][1]:
                raise ValueError('Invalid memory.formation.' + key)
        settings = cls(**data)
        if settings.min_sessions > settings.min_sources or settings.min_sources > settings.max_observations:
            raise ValueError('Formation thresholds exceed their evidence budget')
        if settings.core_half_life_days > settings.retention_days:
            raise ValueError('Core demotion must precede pattern retirement')
        return settings

    def public_dict(self):
        return asdict(self)

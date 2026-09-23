"""Shared counter allocation; the calling service owns commit and rollback."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.modules.stations.models import Station
from app.modules.system.models import IdentifierCounter


def allocate_complaint_reference(db: Session, station: Station, now: datetime) -> str:
    # Station administration must preserve codes once used in issued identifiers.
    if not re.fullmatch(r'[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*', station.station_code):
        raise HTTPException(409, 'Station code is not configured for complaint references')
    year = now.astimezone(ZoneInfo('Africa/Johannesburg')).year
    table = IdentifierCounter.__table__
    statement = insert(table).values(counter_type='COMPLAINT', station_id=station.id,
                                     calendar_year=year, last_value=1)
    statement = statement.on_conflict_do_update(
        index_elements=[table.c.counter_type, table.c.station_id, table.c.calendar_year],
        set_={'last_value': table.c.last_value + 1, 'updated_at': func.now()},
    ).returning(table.c.last_value)
    number = db.execute(statement).scalar_one()
    # Six digits is a minimum width; do not wrap or reset at 999999.
    return f'CMP-{station.station_code}-{year}-{number:06d}'

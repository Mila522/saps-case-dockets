"""Explicit, repeatable project station seed. Run: python -m app.modules.stations.seed_kzn"""
import re

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.db.session import SessionLocal
from app.db import models  # noqa: F401 -- existing mappings, never create tables
from app.modules.stations.models import Station

# Internal project codes, NOT official SAPS station codes. Addresses supplied by
# the project owner with official source URLs; no inferred contact information.
STATIONS = (
    dict(station_code='PROJECT-KZN-POINT', name='Point', address_line_1='165 Prince Street',
         city='Durban', source='https://www.saps.gov.za/contacts/stationdetails.php?sid=621'),
    dict(station_code='PROJECT-KZN-DURBAN-NORTH', name='Durban North', address_line_1='4 Norrie Avenue, Durban North',
         city='Durban', source='https://www.saps.gov.za/contacts/stationdetails.php?sid=1232&sname=Durban+North'),
    dict(station_code='PROJECT-KZN-ALEXANDRA-ROAD', name='Alexandra Road', address_line_1='101 Alexandra Road',
         city='Pietermaritzburg', source='https://www.saps.gov.za/contacts/stationdetails.php?sid=653'),
)


def normalized(value):
    return re.sub(r'[^a-z0-9]', '', (value or '').casefold())


def station_name(value):
    value = (value or '').strip().casefold()
    value = re.sub(r'^saps\s+', '', value)
    value = re.sub(r'\s+(?:police station|saps)$', '', value)
    return normalized(value)


def seed(db):
    # Serialize this explicit maintenance operation with other station writes,
    # including a second seed process. Never alter IDs, codes or dependent rows.
    db.execute(text('LOCK TABLE case_mgmt.stations IN SHARE ROW EXCLUSIVE MODE'))
    rows = list(db.scalars(select(Station)))
    result = []
    for supplied in STATIONS:
        candidates = [row for row in rows if row.station_code == supplied['station_code'] or (
            station_name(row.name) == station_name(supplied['name']) and
            normalized(row.province) in {'kwazulunatal', 'kzn'})]
        if len(candidates) > 1:
            raise ValueError(f"Ambiguous existing records for {supplied['name']}; resolve manually without changing linked IDs")
        created = not candidates
        if created:
            row = Station(station_code=supplied['station_code'], name=supplied['name'], province='KwaZulu-Natal')
            db.add(row)
            rows.append(row)
        else:
            row = candidates[0]
            if station_name(row.name) != station_name(supplied['name']):
                raise ValueError(f"Internal project code collision for {supplied['name']}; no records changed")
            if normalized(row.province) not in {'kwazulunatal', 'kzn'}:
                raise ValueError(f"Province conflict for {supplied['name']}; no records changed")
        row.address_line_1 = supplied['address_line_1']
        row.city = supplied['city']
        row.province = 'KwaZulu-Natal'
        row.is_active = True
        db.flush()
        result.append({'id': str(row.id), 'station_code': row.station_code, 'name': row.name, 'created': created})
    return result


def main():
    if settings.environment != 'development':
        raise SystemExit('This seed command is restricted to the configured development environment.')
    try:
        with SessionLocal.begin() as db:
            result = seed(db)
    except SQLAlchemyError:
        raise SystemExit('Station seed failed; transaction rolled back. Check database availability and station write privileges.') from None
    except ValueError as error:
        raise SystemExit(str(error)) from None
    for row in result:
        print(f"{'Created' if row['created'] else 'Reused'} {row['name']}: {row['id']} ({row['station_code']})")


if __name__ == '__main__':
    main()

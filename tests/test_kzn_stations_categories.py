import uuid

import pytest
from sqlalchemy import select

from app.modules.stations.models import Station, Officer
from app.modules.stations.seed_kzn import STATIONS, seed, station_name
from app.modules.complaints.models import Complaint
from app.modules.complaints.categories import CRIME_CATEGORIES
from test_authentication import auth_context
from test_complaint_tracking import account
from test_complaint_registration import payload
from test_decisions_dockets import workflow_context, officer_account, complaint_at_station
from test_intake_corrections import walk_in


def test_seed_repeatable_reuses_existing_ids_codes_and_links(workflow_context):
    client, db = workflow_context
    initial = seed(db); db.commit()
    row = db.get(Station, uuid.UUID(initial[0]['id']))
    # An existing station using a different internal code must be reused by name.
    original_id = row.id
    row.station_code = 'EXISTING-' + uuid.uuid4().hex[:8]
    row.name = '  SAPS Point Police Station  '
    row.province = 'KZN'
    row.is_active = False
    row.phone_number = 'existing-contact'
    original_code = row.station_code
    db.commit()
    _, _, officer = officer_account(client, db, 'CHARGE_OFFICER', row)
    complaint = complaint_at_station(client, db, row)
    original_ref = complaint.reference_number
    first = seed(db); db.commit()
    second = seed(db); db.commit()
    assert [item['id'] for item in first] == [item['id'] for item in second]
    assert all(not item['created'] for item in second)
    db.refresh(row); db.refresh(officer); db.refresh(complaint)
    assert row.id == original_id and row.station_code == original_code
    assert row.is_active and row.phone_number == 'existing-contact'
    assert officer.station_id == original_id and complaint.station_id == original_id
    assert complaint.reference_number == original_ref
    for supplied in STATIONS:
        matches = [item for item in db.scalars(select(Station)) if station_name(item.name)==station_name(supplied['name']) and item.province=='KwaZulu-Natal']
        assert len(matches) == 1
        assert matches[0].city == supplied['city'] and matches[0].address_line_1 == supplied['address_line_1']


def test_seed_conflicts_fail_without_merging(workflow_context):
    _, db = workflow_context
    seed(db); db.commit()
    duplicate = Station(station_code='DUP-'+uuid.uuid4().hex, name='Point', province='KwaZulu-Natal')
    db.add(duplicate); db.commit()
    with pytest.raises(ValueError, match='Ambiguous'):
        with db.begin_nested(): seed(db)
    assert db.get(Station, duplicate.id) is not None


def test_seeded_station_directory_contains_address_and_locality(auth_context):
    client, db = auth_context
    seed(db); db.commit()
    headers, _, _ = account(client, db)
    result = client.get('/api/v1/complaints/receiving-stations', headers=headers)
    assert result.status_code == 200
    rows = result.json()
    for supplied in STATIONS:
        station = next(row for row in rows if station_name(row['name'])==station_name(supplied['name']))
        assert station['city']==supplied['city'] and station['address_line_1']==supplied['address_line_1']


@pytest.mark.parametrize('category', CRIME_CATEGORIES[:-1] + ('Other: Unlisted incident', 'Other: ' + 'x'*143))
def test_categories_save_with_station_and_incident_details(auth_context, category):
    client, db = auth_context
    headers, _, _ = account(client, db)
    station_id = seed(db)[0]['id']; db.commit()
    data = dict(station_id=station_id, crime_category=category, incident_description='My original statement',
        incident_location='Different incident street', incident_city='Another city', incident_province='Gauteng')
    response = client.post('/api/v1/complaints', headers=headers, json=data)
    assert response.status_code == 201
    saved = db.get(Complaint, uuid.UUID(response.json()['id']))
    assert str(saved.station_id)==station_id and saved.crime_category==category
    assert saved.incident_location=='Different incident street' and saved.incident_city=='Another city'
    assert saved.incident_province=='Gauteng' and saved.incident_description=='My original statement'


@pytest.mark.parametrize('category', ['', 'Other', 'Other: ', 'Other: '+'x'*144, 'Legacy arbitrary category'])
def test_invalid_new_categories_rejected_in_both_requests(auth_context, category):
    client, db = auth_context
    headers, _, _ = account(client, db)
    data = payload(db, crime_category=category)
    assert client.post('/api/v1/complaints', headers=headers, json=data).status_code==422
    from app.modules.complaints.schemas import InStationRegistration
    from pydantic import ValidationError
    with pytest.raises(ValidationError): InStationRegistration(**walk_in(crime_category=category))


def test_missing_station_is_rejected(auth_context):
    client, db = auth_context
    headers, _, _ = account(client, db)
    data = payload(db); del data['station_id']
    assert client.post('/api/v1/complaints', headers=headers, json=data).status_code == 422


def test_officer_categories_and_historical_free_text_remain_readable(workflow_context):
    client, db = workflow_context
    station = db.get(Station, uuid.UUID(seed(db)[0]['id'])); db.commit()
    headers, _, _ = officer_account(client, db, 'CHARGE_OFFICER', station)
    for category in (*CRIME_CATEGORIES[:-1], 'Other: Local report'):
        result = client.post('/api/v1/complaints/in-station', headers=headers, json=walk_in(crime_category=category))
        assert result.status_code==201 and result.json()['crime_category']==category
    legacy = complaint_at_station(client, db, station)
    legacy.crime_category = 'Historical free text'; db.commit()
    rows = client.get('/api/v1/complaints/station', headers=headers).json()['items']
    assert next(row for row in rows if row['id']==str(legacy.id))['crime_category']=='Historical free text'

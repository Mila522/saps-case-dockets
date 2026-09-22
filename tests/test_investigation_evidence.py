"""PostgreSQL integration tests; every fixture write is rolled back."""
import re
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import BigInteger, CheckConstraint, UniqueConstraint, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, configure_mappers

from app.db.session import engine
from app.modules.access.models import User
from app.modules.complainants.models import Complainant
from app.modules.complaints.models import Complaint
from app.modules.dockets.models import Docket
from app.modules.evidence.models import EvidenceCustodyEvent, EvidenceFile, EvidenceItem
from app.modules.investigations.models import CaseAssignment, DocketStatusHistory, InvestigationNote
from app.modules.refusals.models import ComplaintDecision
from app.modules.stations.models import Officer, Station


NEW_MODELS = (CaseAssignment, DocketStatusHistory, InvestigationNote, EvidenceItem, EvidenceFile, EvidenceCustodyEvent)


class InvestigationEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.conn = engine.connect()
        self.transaction = self.conn.begin()
        self.addCleanup(self.conn.close)
        self.addCleanup(self.transaction.rollback)
        self.user = self.insert(User, username=uuid.uuid4().hex, email=uuid.uuid4().hex + "@example.invalid", password_hash="!")
        station = self.insert(Station, station_code=uuid.uuid4().hex, name="Test", province="Test")
        self.officer = self.insert(Officer, user_id=self.user, station_id=station, service_number=uuid.uuid4().hex, rank="Test")
        person = self.insert(Complainant, first_name="Test", last_name="Test", phone_number="000", preferred_contact_method="SMS")
        complaint = self.insert(Complaint, reference_number=uuid.uuid4().hex, complainant_id=person, station_id=station,
                                channel="ONLINE", status="ACCEPTED", crime_category="Test", incident_description="Test",
                                incident_location="Test", incident_province="Test")
        decision = self.insert(ComplaintDecision, complaint_id=complaint, decision="ACCEPTED", decided_by_officer_id=self.officer)
        self.docket = self.insert(Docket, complaint_id=complaint, cas_number=uuid.uuid4().hex,
                                 created_from_decision_id=decision, opened_by_officer_id=self.officer)

    def insert(self, model, **values):
        return self.conn.execute(model.__table__.insert().values(**values).returning(model.id)).scalar_one()

    def rejected(self, statement, code="23514"):
        with self.assertRaises(DBAPIError) as raised:
            with self.conn.begin_nested():
                self.conn.execute(statement)
        self.assertEqual(raised.exception.orig.sqlstate, code)

    def evidence_values(self, **changes):
        return dict(docket_id=self.docket, evidence_reference="EV-" + uuid.uuid4().hex,
                    title="Test", description="Test", evidence_type="IMAGE", is_digital=True,
                    registered_by_user_id=self.user) | changes

    def test_schema_indexes_and_relationships(self):
        configure_mappers()
        inspector = inspect(self.conn)
        for model in NEW_MODELS:
            table = model.__table__
            self.assertEqual(table.schema, "case_mgmt")
            self.conn.execute(select(table).limit(1))
            checks = {c["name"] for c in inspector.get_check_constraints(table.name, schema="case_mgmt")}
            self.assertEqual(checks, {c.name for c in table.constraints if isinstance(c, CheckConstraint)})
            indexes = {i["name"]: i for i in inspector.get_indexes(table.name, schema="case_mgmt")}
            for index in table.indexes:
                self.assertIn(index.name, indexes)
                self.assertEqual(indexes[index.name]["unique"], index.unique)
                self.assertEqual(indexes[index.name]["column_names"], [c.name for c in index.columns])
                predicate = index.dialect_options["postgresql"]["where"]
                if predicate is not None:
                    self.assertEqual(indexes[index.name]["dialect_options"]["postgresql_where"].strip("()"), str(predicate))
            uniques = {tuple(c["column_names"]) for c in inspector.get_unique_constraints(table.name, schema="case_mgmt")}
            for constraint in table.constraints:
                if isinstance(constraint, UniqueConstraint):
                    self.assertIn(tuple(c.name for c in constraint.columns), uniques)
            for fk in inspector.get_foreign_keys(table.name, schema="case_mgmt"):
                self.assertEqual(fk["referred_schema"], "case_mgmt")
                self.assertEqual(fk["options"]["ondelete"], "RESTRICT")
        for model in (DocketStatusHistory, InvestigationNote, EvidenceFile, EvidenceCustodyEvent):
            self.assertNotIn("updated_at", {c["name"] for c in inspector.get_columns(model.__tablename__, schema="case_mgmt")})
        size = next(c for c in inspector.get_columns("evidence_files", schema="case_mgmt") if c["name"] == "file_size_bytes")
        self.assertIsInstance(size["type"], BigInteger)
        docket_statuses = next(c for c in Docket.__table__.constraints if isinstance(c, CheckConstraint))
        expected = set(re.findall("'([^']+)'", str(docket_statuses.sqltext)))
        for name in ("from_status", "to_status"):
            constraint = next(c for c in DocketStatusHistory.__table__.constraints if c.name == "ck_docket_status_history_" + name)
            self.assertEqual(set(re.findall("'([^']+)'", str(constraint.sqltext))), expected)

    def test_assignment_history_and_dates(self):
        now = datetime.now(timezone.utc)
        values = dict(docket_id=self.docket, investigating_officer_id=self.officer,
                      assigned_by_officer_id=self.officer, assigned_at=now)
        first = self.insert(CaseAssignment, **values)
        self.rejected(CaseAssignment.__table__.insert().values(**values), "23505")
        self.rejected(CaseAssignment.__table__.insert().values(**values, unassigned_at=now - timedelta(seconds=1)))
        self.conn.execute(CaseAssignment.__table__.update().where(CaseAssignment.id == first).values(unassigned_at=now, unassignment_reason="Reassigned"))
        self.insert(CaseAssignment, **values)
        self.assertEqual(len(self.conn.execute(select(CaseAssignment.id).where(CaseAssignment.docket_id == self.docket)).all()), 2)
        self.rejected(CaseAssignment.__table__.insert().values(**(values | {"docket_id": uuid.uuid4()})), "23503")
        self.rejected(Docket.__table__.delete().where(Docket.id == self.docket), "23001")

    def test_status_history_initial_record(self):
        now = datetime.now(timezone.utc)
        values = dict(docket_id=self.docket, changed_by_user_id=self.user, to_status="PENDING_APPROVAL", changed_at=now)
        initial = self.insert(DocketStatusHistory, **values)
        self.rejected(DocketStatusHistory.__table__.insert().values(**values))
        for changes in ({"from_status": "WRONG"}, {"to_status": "WRONG", "from_status": "ACTIVE"},
                        {"from_status": "ACTIVE", "to_status": "ACTIVE"},
                        {"from_status": "PENDING_APPROVAL", "to_status": "APPROVED", "changed_at": now - timedelta(seconds=1)}):
            self.rejected(DocketStatusHistory.__table__.insert().values(**(values | changes)))
        self.insert(DocketStatusHistory, **(values | {"from_status": "PENDING_APPROVAL", "to_status": "APPROVED", "changed_at": now + timedelta(seconds=1)}))
        self.rejected(DocketStatusHistory.__table__.update().where(DocketStatusHistory.id == initial).values(changed_at=now + timedelta(seconds=2)), "42501")
        self.assertEqual(self.conn.execute(select(Docket.status).where(Docket.id == self.docket)).scalar_one(), "PENDING_APPROVAL")

    def test_initial_history_cannot_follow_non_initial(self):
        values = dict(docket_id=self.docket, changed_by_user_id=self.user, to_status="APPROVED")
        self.insert(DocketStatusHistory, **values, from_status="PENDING_APPROVAL")
        self.rejected(DocketStatusHistory.__table__.insert().values(**values))

    def test_evidence_file_integrity(self):
        item = self.insert(EvidenceItem, **self.evidence_values())
        values = dict(evidence_item_id=item, original_filename="photo.jpg", storage_key="protected/" + uuid.uuid4().hex,
                      media_type="image/jpeg", file_size_bytes=2**33, sha256_hash="a" * 64, uploaded_by_user_id=self.user)
        self.insert(EvidenceFile, **values)
        self.rejected(EvidenceFile.__table__.insert().values(**(values | {"storage_key": "protected/second"})), "23505")
        self.rejected(EvidenceFile.__table__.insert().values(**values, file_version=2), "23505")
        valid_second = values | {"storage_key": "protected/second", "file_version": 2}
        for changes in ({"file_size_bytes": 0}, {"file_size_bytes": -1}, {"file_version": 0},
                        {"sha256_hash": "a" * 63}, {"sha256_hash": "g" * 64},
                        {"storage_key": "https://example.invalid/file"}, {"storage_key": "//example.invalid/file"},
                        {"storage_key": "C:\\file"}, {"storage_key": "   "}):
            self.rejected(EvidenceFile.__table__.insert().values(**(valid_second | changes)))
        self.insert(EvidenceFile, **(valid_second | {"sha256_hash": "A" * 64}))
        self.rejected(EvidenceItem.__table__.delete().where(EvidenceItem.id == item), "23001")

    def test_notes_evidence_and_custody_relationships(self):
        note_values = dict(docket_id=self.docket, author_officer_id=self.officer, note_type="GENERAL", content="Original")
        self.insert(InvestigationNote, **note_values)
        self.insert(InvestigationNote, **(note_values | {"note_type": "CORRECTION", "content": "Correction"}))
        self.rejected(InvestigationNote.__table__.insert().values(**(note_values | {"note_type": "INVALID"})))
        item_values = self.evidence_values(collected_by_officer_id=self.officer, current_custodian_officer_id=self.officer)
        item = self.insert(EvidenceItem, **item_values)
        self.rejected(EvidenceItem.__table__.insert().values(**item_values), "23505")
        for changes in ({"evidence_type": "INVALID"}, {"status": "INVALID"}):
            self.rejected(EvidenceItem.__table__.insert().values(**self.evidence_values(**changes)))
        event_values = dict(evidence_item_id=item, event_type="TRANSFERRED", performed_by_user_id=self.user,
                            from_custodian_officer_id=self.officer, to_custodian_officer_id=self.officer)
        event = self.insert(EvidenceCustodyEvent, **event_values)
        self.rejected(EvidenceCustodyEvent.__table__.insert().values(**(event_values | {"event_type": "INVALID"})))
        self.rejected(EvidenceCustodyEvent.__table__.insert().values(**(event_values | {"to_custodian_officer_id": uuid.uuid4()})), "23503")
        self.insert(CaseAssignment, docket_id=self.docket, investigating_officer_id=self.officer, assigned_by_officer_id=self.officer)
        with Session(bind=self.conn, join_transaction_mode="create_savepoint") as session:
            docket = session.get(Docket, self.docket)
            self.assertEqual(docket.assignments[0].investigating_officer.id, self.officer)
            self.assertEqual(docket.assignments[0].assigned_by_officer.id, self.officer)
            self.assertEqual(len(docket.investigation_notes), 2)
            self.assertFalse(docket.investigation_notes[0].is_sensitive)
            evidence = docket.evidence_items[0]
            self.assertEqual(evidence.registered_by_user.id, self.user)
            self.assertEqual(evidence.collected_by_officer.id, self.officer)
            self.assertEqual(evidence.current_custodian_officer.id, self.officer)
            custody = session.get(EvidenceCustodyEvent, event)
            self.assertEqual(custody.from_custodian_officer.id, self.officer)
            self.assertEqual(custody.to_custodian_officer.id, self.officer)
            self.assertEqual(custody.performed_by_user.id, self.user)

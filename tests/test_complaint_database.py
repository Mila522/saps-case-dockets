"""PostgreSQL integration tests. All fixture writes are rolled back."""
import unittest
import uuid

from sqlalchemy import CheckConstraint, UniqueConstraint, inspect, select, text
from sqlalchemy.exc import DBAPIError

from app.db.session import engine
from app.modules.access.models import User
from app.modules.complainants.models import Complainant
from app.modules.complaints.models import Complaint, ComplaintStatement, Witness, WitnessStatement
from app.modules.dockets.models import Docket, DocketApproval
from app.modules.refusals.models import ComplaintDecision, RefusalEscalation, RefusalReason
from app.modules.stations.models import Officer, Station


class ComplaintDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.conn = engine.connect()
        self.transaction = self.conn.begin()
        self.addCleanup(self.conn.close)
        self.addCleanup(self.transaction.rollback)
        self.station = self.insert(Station, station_code=uuid.uuid4().hex, name="Test", province="Test")
        user = self.insert(User, username=uuid.uuid4().hex, email=uuid.uuid4().hex + "@example.invalid", password_hash="!")
        self.officer = self.insert(Officer, user_id=user, station_id=self.station, service_number=uuid.uuid4().hex, rank="Test")
        self.person = self.insert(Complainant, first_name="Test", last_name="Test", phone_number="000", preferred_contact_method="SMS")

    def insert(self, model, **values):
        return self.conn.execute(model.__table__.insert().values(**values).returning(model.id)).scalar_one()

    def complaint_values(self, **changes):
        return dict(reference_number="TEST-" + uuid.uuid4().hex, complainant_id=self.person,
                    station_id=self.station, channel="ONLINE", crime_category="Test",
                    incident_description="Test", incident_location="Test", incident_province="Test") | changes

    def complaint(self, **changes):
        return self.insert(Complaint, **self.complaint_values(**changes))

    def decision(self, complaint, **changes):
        return self.insert(ComplaintDecision, **(dict(complaint_id=complaint, decision="ACCEPTED",
                          decided_by_officer_id=self.officer) | changes))

    def rejected(self, statement, sqlstate="23514"):
        with self.assertRaises(DBAPIError) as raised:
            with self.conn.begin_nested():
                self.conn.execute(statement)
        self.assertEqual(raised.exception.orig.sqlstate, sqlstate)

    def test_schema_constraints_and_seeds(self):
        inspector = inspect(self.conn)
        new = [Complaint, ComplaintStatement, Witness, WitnessStatement, RefusalReason,
               ComplaintDecision, RefusalEscalation, Docket, DocketApproval]
        for model in new:
            table = model.__table__
            self.assertEqual(table.schema, "case_mgmt")
            actual = {c["name"] for c in inspector.get_check_constraints(table.name, schema="case_mgmt")}
            expected = {c.name for c in table.constraints if isinstance(c, CheckConstraint)}
            self.assertEqual(actual, expected)
            uniques = {tuple(c["column_names"]) for c in inspector.get_unique_constraints(table.name, schema="case_mgmt")}
            for constraint in table.constraints:
                if isinstance(constraint, UniqueConstraint):
                    self.assertIn(tuple(c.name for c in constraint.columns), uniques)
            for fk in inspector.get_foreign_keys(table.name, schema="case_mgmt"):
                self.assertEqual(fk["referred_schema"], "case_mgmt")
                self.assertEqual(fk["options"]["ondelete"], "RESTRICT")
            self.conn.execute(select(table).limit(1))
        expected_codes = ["SUSPECT_UNKNOWN", "SUSPECT_NOT_PRESENT", "OUTSIDE_JURISDICTION", "MATTER_NOT_SERIOUS",
                          "DUPLICATE_COMPLAINT", "NO_CRIMINAL_OFFENCE_IDENTIFIED", "OTHER"]
        seeds = self.conn.execute(select(RefusalReason.__table__).order_by(RefusalReason.id)).mappings().all()
        self.assertEqual([r["code"] for r in seeds], expected_codes)
        for i, row in enumerate(seeds, 1):
            self.assertEqual(row["id"], uuid.UUID(f"30000000-0000-4000-8000-{i:012d}"))
            self.assertEqual(row["is_non_compliant"], i <= 4)
            self.assertEqual(row["requires_escalation"], i <= 4 or i == 7)
            self.assertEqual(row["requires_officer_notes"], i >= 5)
            self.assertTrue(row["is_active"])
        self.assertNotIn("updated_at", ComplaintDecision.__table__.c)
        self.assertNotIn("updated_at", DocketApproval.__table__.c)
        self.assertFalse(self.conn.execute(text("""
            SELECT EXISTS (SELECT 1 FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace
            WHERE n.nspname='case_mgmt' AND NOT c.convalidated)
        """)).scalar_one())

    def test_registration_and_references(self):
        self.complaint()
        self.complaint(channel="IN_STATION", registered_by_officer_id=self.officer)
        for changes in ({"channel": "IN_STATION"}, {"channel": "PHONE"}, {"status": "INVALID"}):
            self.rejected(Complaint.__table__.insert().values(**self.complaint_values(**changes)))
        self.rejected(Complaint.__table__.insert().values(**self.complaint_values(reference_number=None)), "23502")
        values = self.complaint_values()
        self.insert(Complaint, **values)
        self.rejected(Complaint.__table__.insert().values(**values), "23505")
        self.rejected(Complaint.__table__.insert().values(**self.complaint_values(complainant_id=uuid.uuid4())), "23503")
        for model, key in ((Officer, self.officer), (Station, self.station), (Complainant, self.person)):
            self.rejected(model.__table__.delete().where(model.id == key), "23001")

    def test_statement_history(self):
        complaint = self.complaint()
        witness = self.insert(Witness, complaint_id=complaint, first_name="Test", last_name="Witness")
        for model, key, owner in ((ComplaintStatement, "complaint_id", complaint), (WitnessStatement, "witness_id", witness)):
            values = {key: owner, "statement_text": "Original"}
            first = self.insert(model, **values)
            self.rejected(model.__table__.insert().values(**values), "23505")
            self.rejected(model.__table__.insert().values(**values, statement_version=2), "23505")
            self.rejected(model.__table__.insert().values(**values, statement_version=0, is_current=False))
            self.rejected(model.__table__.update().where(model.id == first).values(statement_text="Overwritten"), "42501")
            self.rejected(model.__table__.delete().where(model.id == first), "42501")
            self.conn.execute(model.__table__.update().where(model.id == first).values(is_current=False))
            self.insert(model, **{key: owner, "statement_text": "Correction", "statement_version": 2})
            self.assertEqual(self.conn.execute(select(model.statement_text).where(model.id == first)).scalar_one(), "Original")

    def test_decisions_and_escalations(self):
        complaint = self.complaint(status="REFUSED")
        other = self.complaint(status="ACCEPTED")
        reason = self.conn.execute(select(RefusalReason.id).where(RefusalReason.code == "SUSPECT_UNKNOWN")).scalar_one()
        values = dict(complaint_id=complaint, decided_by_officer_id=self.officer)
        self.rejected(ComplaintDecision.__table__.insert().values(**values, decision="REFUSED"))
        self.rejected(ComplaintDecision.__table__.insert().values(**values, decision="ACCEPTED", refusal_reason_id=reason))
        refused = self.decision(complaint, decision="REFUSED", refusal_reason_id=reason)
        accepted = self.decision(other)
        self.rejected(ComplaintDecision.__table__.insert().values(**values, decision="ACCEPTED"), "23505")
        for model, key in ((ComplaintDecision, refused),):
            self.rejected(model.__table__.update().where(model.id == key).values(officer_notes="Change"), "42501")
            self.rejected(model.__table__.delete().where(model.id == key), "42501")
        escalation = dict(complaint_id=complaint, complaint_decision_id=refused)
        for target in ("STATION_COMMANDER", "NCC"):
            self.insert(RefusalEscalation, **escalation, target=target)
        self.rejected(RefusalEscalation.__table__.insert().values(**escalation, target="NCC"), "23505")
        self.rejected(RefusalEscalation.__table__.insert().values(**escalation, target="INVALID"))
        for c, d in ((other, refused), (other, accepted)):
            self.rejected(RefusalEscalation.__table__.insert().values(complaint_id=c, complaint_decision_id=d, target="NCC"))
        self.decision(complaint, decision_sequence=2)
        self.assertEqual(self.conn.execute(select(ComplaintDecision.decision).where(ComplaintDecision.id == refused)).scalar_one(), "REFUSED")

    def test_dockets_require_acceptance_and_preserve_approvals(self):
        complaint = self.complaint()
        accepted = self.decision(complaint)
        values = dict(complaint_id=complaint, created_from_decision_id=accepted,
                      cas_number="CAS-" + uuid.uuid4().hex, opened_by_officer_id=self.officer)
        self.rejected(Docket.__table__.insert().values(**values))
        self.conn.execute(Complaint.__table__.update().where(Complaint.id == complaint).values(status="ACCEPTED"))
        self.rejected(Docket.__table__.insert().values(**(values | {"cas_number": None})), "23502")
        docket = self.insert(Docket, **values)
        self.conn.execute(Complaint.__table__.update().where(Complaint.id == complaint).values(status="DOCKET_CREATED"))
        self.rejected(Docket.__table__.insert().values(**(values | {"cas_number": "DIFFERENT"})), "23505")
        other = self.complaint(status="ACCEPTED")
        other_decision = self.decision(other)
        self.rejected(Docket.__table__.insert().values(**(values | {"complaint_id": other, "created_from_decision_id": other_decision})), "23505")
        self.rejected(Docket.__table__.insert().values(**(values | {"complaint_id": other, "cas_number": "DIFFERENT"})))
        self.rejected(Complaint.__table__.update().where(Complaint.id == complaint).values(status="REFUSED"))
        reason = self.conn.execute(select(RefusalReason.id).limit(1)).scalar_one()
        refused = self.decision(other, decision="REFUSED", refusal_reason_id=reason, decision_sequence=2)
        self.rejected(Docket.__table__.insert().values(**(values | {"complaint_id": other, "created_from_decision_id": refused, "cas_number": "DIFFERENT"})))
        approval_values = dict(docket_id=docket, decided_by_officer_id=self.officer, decision="RETURNED_FOR_CORRECTION")
        first = self.insert(DocketApproval, **approval_values)
        self.rejected(DocketApproval.__table__.insert().values(**approval_values), "23505")
        self.insert(DocketApproval, **(approval_values | {"decision": "APPROVED", "decision_sequence": 2}))
        self.rejected(DocketApproval.__table__.update().where(DocketApproval.id == first).values(decision="APPROVED"), "42501")
        self.rejected(DocketApproval.__table__.delete().where(DocketApproval.id == first), "42501")

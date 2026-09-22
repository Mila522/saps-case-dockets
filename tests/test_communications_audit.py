"""Rollback-only PostgreSQL tests, including owner-level trigger verification."""
import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, CheckConstraint, UniqueConstraint, create_engine, inspect, select, text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import engine
from app.modules.access.models import User
from app.modules.alerts.models import Alert
from app.modules.audit.models import AuditLog
from app.modules.communications.models import CaseFeedback, Notification, NotificationAttempt, OfficialDocument
from app.modules.complainants.models import Complainant
from app.modules.complaints.models import Complaint
from app.modules.dockets.models import Docket, DocketApproval
from app.modules.evidence.models import EvidenceCustodyEvent, EvidenceFile, EvidenceItem
from app.modules.investigations.models import DocketStatusHistory, InvestigationNote
from app.modules.refusals.models import ComplaintDecision
from app.modules.stations.models import Officer, Station


NEW_MODELS = (CaseFeedback, OfficialDocument, Notification, NotificationAttempt, Alert, AuditLog)
HISTORY_MODELS = (ComplaintDecision, DocketApproval, DocketStatusHistory, InvestigationNote,
                  EvidenceFile, EvidenceCustodyEvent, CaseFeedback, OfficialDocument, NotificationAttempt, AuditLog)


def insert(conn, model, **values):
    return conn.execute(model.__table__.insert().values(**values).returning(model.id)).scalar_one()


def fixture_graph(conn):
    """Supply real rows so BEFORE ROW triggers, rather than empty updates, are tested."""
    rows = {}
    def add(model, **values):
        rows[model] = insert(conn, model, **values)
        return rows[model]
    user = add(User, username=uuid.uuid4().hex, email=uuid.uuid4().hex + "@example.invalid", password_hash="!")
    station = add(Station, station_code=uuid.uuid4().hex, name="Test", province="Test")
    officer = add(Officer, user_id=user, station_id=station, service_number=uuid.uuid4().hex, rank="Test")
    person = add(Complainant, first_name="Test", last_name="Test", phone_number="000", preferred_contact_method="SMS")
    complaint = add(Complaint, reference_number=uuid.uuid4().hex, complainant_id=person, station_id=station,
                    channel="ONLINE", status="ACCEPTED", crime_category="Test", incident_description="Test",
                    incident_location="Test", incident_province="Test")
    decision = add(ComplaintDecision, complaint_id=complaint, decision="ACCEPTED", decided_by_officer_id=officer)
    docket = add(Docket, complaint_id=complaint, cas_number=uuid.uuid4().hex, created_from_decision_id=decision, opened_by_officer_id=officer)
    add(DocketApproval, docket_id=docket, decided_by_officer_id=officer, decision="APPROVED")
    add(DocketStatusHistory, docket_id=docket, to_status="PENDING_APPROVAL", changed_by_user_id=user)
    add(InvestigationNote, docket_id=docket, author_officer_id=officer, note_type="GENERAL", content="Test")
    item = add(EvidenceItem, docket_id=docket, evidence_reference=uuid.uuid4().hex, title="Test", description="Test",
               evidence_type="IMAGE", is_digital=True, registered_by_user_id=user)
    add(EvidenceFile, evidence_item_id=item, original_filename="test.jpg", storage_key="test/" + uuid.uuid4().hex,
        media_type="image/jpeg", file_size_bytes=1, sha256_hash="a" * 64, uploaded_by_user_id=user)
    add(EvidenceCustodyEvent, evidence_item_id=item, event_type="REGISTERED", performed_by_user_id=user)
    add(CaseFeedback, docket_id=docket, complainant_id=person, provided_by_officer_id=officer,
        feedback_type="GENERAL", subject="Test", message="Original")
    add(OfficialDocument, complaint_id=complaint, document_type="COMPLAINT_REGISTRATION_CONFIRMATION",
        document_number=uuid.uuid4().hex, storage_key="test/" + uuid.uuid4().hex,
        media_type="application/pdf", file_size_bytes=2**33, sha256_hash="a" * 64, generated_by_user_id=user)
    notification = add(Notification, recipient_user_id=user, channel="IN_APP", event_type="TEST", message="Test")
    add(NotificationAttempt, notification_id=notification, attempt_number=1, provider="test", outcome="SENT", response_metadata={"accepted": True})
    add(Alert, station_id=station, alert_type="SYSTEM_COMPLIANCE", severity="LOW", title="Test", description="Test")
    add(AuditLog, actor_type="USER", actor_user_id=user, action="TEST", entity_type="docket", entity_id=docket,
        ip_address="2001:db8::1", event_metadata={"test": True})
    return rows


class CommunicationsAuditTests(unittest.TestCase):
    def setUp(self):
        self.conn = engine.connect()
        self.transaction = self.conn.begin()
        self.addCleanup(self.conn.close)
        self.addCleanup(self.transaction.rollback)
        self.rows = fixture_graph(self.conn)

    def rejected(self, statement, code="23514", conn=None):
        connection = conn if conn is not None else self.conn
        with self.assertRaises(DBAPIError) as raised:
            with connection.begin_nested():
                connection.execute(statement)
        self.assertEqual(raised.exception.orig.sqlstate, code)
        return str(raised.exception.orig)

    def test_schema_types_indexes_and_triggers(self):
        ins = inspect(self.conn)
        for model in NEW_MODELS:
            table = model.__table__
            self.assertEqual(table.schema, "case_mgmt")
            checks = {c["name"] for c in ins.get_check_constraints(table.name, schema="case_mgmt")}
            self.assertEqual(checks, {c.name for c in table.constraints if isinstance(c, CheckConstraint)})
            indexes = {i["name"]: i for i in ins.get_indexes(table.name, schema="case_mgmt")}
            for index in table.indexes:
                self.assertEqual(indexes[index.name]["column_names"], [c.name for c in index.columns])
                self.assertEqual(indexes[index.name]["unique"], index.unique)
            uniques = {tuple(c["column_names"]) for c in ins.get_unique_constraints(table.name, schema="case_mgmt")}
            for constraint in table.constraints:
                if isinstance(constraint, UniqueConstraint):
                    self.assertIn(tuple(c.name for c in constraint.columns), uniques)
            for fk in ins.get_foreign_keys(table.name, schema="case_mgmt"):
                self.assertEqual(fk["referred_schema"], "case_mgmt")
                self.assertEqual(fk["options"]["ondelete"], "RESTRICT")
            columns = {c["name"]: c for c in ins.get_columns(table.name, schema="case_mgmt")}
            if model not in (Notification, Alert):
                self.assertNotIn("updated_at", columns)
        for model, column, expected in ((AuditLog, "ip_address", INET), (AuditLog, "old_values", JSONB),
                                        (AuditLog, "new_values", JSONB), (AuditLog, "event_metadata", JSONB),
                                        (NotificationAttempt, "response_metadata", JSONB), (OfficialDocument, "file_size_bytes", BigInteger)):
            columns = {c["name"]: c for c in ins.get_columns(model.__tablename__, schema="case_mgmt")}
            self.assertIsInstance(columns[column]["type"], expected)
        triggers = self.conn.execute(text("""
            SELECT c.relname, pg_get_triggerdef(t.oid), t.tgenabled
            FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='case_mgmt' AND t.tgname='aa_append_only'
        """)).all()
        self.assertEqual({r[0] for r in triggers}, {m.__tablename__ for m in HISTORY_MODELS})
        for _, definition, enabled in triggers:
            self.assertIn("BEFORE DELETE OR UPDATE", definition)
            self.assertIn("reject_append_only_mutation()", definition)
            self.assertEqual(enabled, "O")

    def test_runtime_history_privileges_and_mutations(self):
        for model in HISTORY_MODELS:
            name = "case_mgmt." + model.__tablename__
            privileges = dict(self.conn.execute(text("""
                SELECT p, has_table_privilege('saps_api', :table, p)
                FROM unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE']) p
            """), {"table": name}).all())
            self.assertEqual(privileges, {"SELECT": True, "INSERT": True, "UPDATE": False, "DELETE": False, "TRUNCATE": False})
            self.rejected(model.__table__.update().where(model.id == self.rows[model]).values(created_at=model.created_at), "42501")
            self.rejected(model.__table__.delete().where(model.id == self.rows[model]), "42501")
        for model in (Notification, Alert):
            self.conn.execute(model.__table__.update().where(model.id == self.rows[model]).values(status="SENT" if model is Notification else "ACKNOWLEDGED"))

    def test_owner_cannot_update_or_delete_history_with_triggers_enabled(self):
        owner_engine = create_engine(settings.migration_database_url)
        try:
            with owner_engine.connect() as conn:
                transaction = conn.begin()
                try:
                    rows = fixture_graph(conn)
                    for model in HISTORY_MODELS:
                        for statement in (model.__table__.update().where(model.id == rows[model]).values(created_at=model.created_at),
                                          model.__table__.delete().where(model.id == rows[model])):
                            message = self.rejected(statement, "55000", conn)
                            self.assertIn(model.__tablename__ + " is append-only", message)
                finally:
                    transaction.rollback()
        finally:
            owner_engine.dispose()

    def test_feedback_corrections_and_relationships(self):
        values = dict(docket_id=self.rows[Docket], complainant_id=self.rows[Complainant], provided_by_officer_id=self.rows[Officer],
                      feedback_type="GENERAL", subject="Correction", message="Corrected", supersedes_feedback_id=self.rows[CaseFeedback])
        correction = insert(self.conn, CaseFeedback, **values)
        own_id = uuid.uuid4()
        self.rejected(CaseFeedback.__table__.insert().values(**(values | {"id": own_id, "supersedes_feedback_id": own_id})))
        self.rejected(CaseFeedback.__table__.insert().values(**(values | {"feedback_type": "INVALID"})))
        with Session(bind=self.conn, join_transaction_mode="create_savepoint") as session:
            feedback = session.get(CaseFeedback, correction)
            self.assertTrue(feedback.is_official)
            self.assertEqual(feedback.supersedes_feedback.message, "Original")
            self.assertEqual(feedback.provided_by_officer.id, self.rows[Officer])
            self.assertEqual(feedback.complainant.id, self.rows[Complainant])
            self.assertEqual(feedback.docket.id, self.rows[Docket])
            notification = session.get(Notification, self.rows[Notification])
            self.assertEqual(notification.attempts[0].response_metadata, {"accepted": True})
            self.assertEqual(notification.recipient_user.id, self.rows[User])
            audit = session.get(AuditLog, self.rows[AuditLog])
            self.assertEqual(str(audit.ip_address), "2001:db8::1")
            self.assertEqual(audit.event_metadata, {"test": True})

    def test_document_validation_and_uniqueness(self):
        values = dict(docket_id=self.rows[Docket], document_type="CASE_CLOSURE_CONFIRMATION", document_number=uuid.uuid4().hex,
                      storage_key="test/" + uuid.uuid4().hex, media_type="application/pdf", file_size_bytes=1,
                      sha256_hash="a" * 64, generated_by_user_id=self.rows[User])
        for change in ({"docket_id": None}, {"file_size_bytes": 0}, {"sha256_hash": "z" * 64}, {"sha256_hash": "a" * 63},
                       {"storage_key": "https://example.invalid/doc"}, {"storage_key": "//example.invalid/doc"},
                       {"document_type": "INVALID"}):
            self.rejected(OfficialDocument.__table__.insert().values(**(values | change)))
        insert(self.conn, OfficialDocument, **values)
        self.rejected(OfficialDocument.__table__.insert().values(**(values | {"storage_key": "test/second"})), "23505")
        self.rejected(OfficialDocument.__table__.insert().values(**(values | {"document_number": "SECOND"})), "23505")

    def test_notification_recipients_and_attempts(self):
        values = dict(channel="SMS", event_type="TEST", message="Test")
        self.rejected(Notification.__table__.insert().values(**values))
        self.rejected(Notification.__table__.insert().values(**values, recipient_user_id=self.rows[User], recipient_complainant_id=self.rows[Complainant]))
        insert(self.conn, Notification, **values, recipient_complainant_id=self.rows[Complainant])
        for change in ({"channel": "INVALID"}, {"status": "INVALID"}):
            self.rejected(Notification.__table__.insert().values(**(values | change), recipient_user_id=self.rows[User]))
        attempt = dict(notification_id=self.rows[Notification], attempt_number=2, provider="Test", outcome="FAILED")
        self.rejected(NotificationAttempt.__table__.insert().values(**(attempt | {"attempt_number": 0})))
        self.rejected(NotificationAttempt.__table__.insert().values(**(attempt | {"outcome": "INVALID"})))
        self.rejected(NotificationAttempt.__table__.insert().values(**(attempt | {"attempt_number": 1})), "23505")
        insert(self.conn, NotificationAttempt, **attempt)
        self.rejected(Notification.__table__.delete().where(Notification.id == self.rows[Notification]), "23001")

    def test_alert_and_audit_actor_checks(self):
        values = dict(station_id=self.rows[Station], alert_type="CASE_INACTIVITY", severity="HIGH", title="Test", description="Test")
        self.rejected(Alert.__table__.insert().values(**values))
        for change in ({"severity": "INVALID"}, {"status": "INVALID"}, {"alert_type": "INVALID"},
                       {"acknowledged_at": datetime.now(timezone.utc)}, {"resolved_at": datetime.now(timezone.utc)}):
            self.rejected(Alert.__table__.insert().values(**(values | change), docket_id=self.rows[Docket]))
        alert = insert(self.conn, Alert, **values, docket_id=self.rows[Docket], assigned_to_user_id=self.rows[User])
        self.conn.execute(Alert.__table__.update().where(Alert.id == alert).values(status="RESOLVED", resolved_by_user_id=self.rows[User], resolved_at=datetime.now(timezone.utc)))
        audit = dict(action="TEST", entity_type="test")
        self.rejected(AuditLog.__table__.insert().values(**audit, actor_type="USER"))
        self.rejected(AuditLog.__table__.insert().values(**audit, actor_type="INVALID"))
        for actor in ("SYSTEM", "DATABASE"):
            insert(self.conn, AuditLog, **audit, actor_type=actor)

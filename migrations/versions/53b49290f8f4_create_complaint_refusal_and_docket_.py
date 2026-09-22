"""create complaint refusal and docket tables

Revision ID: 53b49290f8f4
Revises: 9d6a130b9b0d
Create Date: 2026-09-21 22:01:24.012030

"""
from typing import Sequence, Union
from uuid import UUID

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '53b49290f8f4'
down_revision: Union[str, Sequence[str], None] = '9d6a130b9b0d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


REFUSAL_SEEDS = [
    {"id": UUID(f"30000000-0000-4000-8000-{i:012d}"), "code": code, "name": name,
     "is_non_compliant": non_compliant, "requires_escalation": escalation,
     "requires_officer_notes": notes, "is_active": True}
    for i, (code, name, non_compliant, escalation, notes) in enumerate([
        ("SUSPECT_UNKNOWN", "Suspect is unknown", True, True, False),
        ("SUSPECT_NOT_PRESENT", "Suspect was not brought to the station", True, True, False),
        ("OUTSIDE_JURISDICTION", "Incident occurred outside station jurisdiction", True, True, False),
        ("MATTER_NOT_SERIOUS", "Matter considered insufficiently serious", True, True, False),
        ("DUPLICATE_COMPLAINT", "Duplicate complaint", False, False, True),
        ("NO_CRIMINAL_OFFENCE_IDENTIFIED", "No criminal offence identified", False, False, True),
        ("OTHER", "Other reason", False, True, True),
    ], 1)
]


def refusal_seed_table():
    return sa.table(
        "refusal_reasons", sa.column("id", sa.UUID()), sa.column("code", sa.String()),
        sa.column("name", sa.String()), sa.column("is_non_compliant", sa.Boolean()),
        sa.column("requires_escalation", sa.Boolean()), sa.column("requires_officer_notes", sa.Boolean()),
        sa.column("is_active", sa.Boolean()), schema="case_mgmt",
    )


def create_integrity_guards():
    # These enforce relational integrity only; they do not create audit logs or
    # implement workflow automation. Cross-table predicates cannot be CHECKs.
    op.execute("""
        CREATE FUNCTION case_mgmt.validate_docket_acceptance() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE complaint_status text;
        BEGIN
            -- Serialize docket creation against concurrent complaint review.
            SELECT status INTO complaint_status FROM case_mgmt.complaints
              WHERE id = NEW.complaint_id FOR UPDATE;
            IF complaint_status IS NULL OR complaint_status NOT IN ('ACCEPTED', 'DOCKET_CREATED')
               OR NOT EXISTS (SELECT 1 FROM case_mgmt.complaint_decisions
                 WHERE id = NEW.created_from_decision_id AND complaint_id = NEW.complaint_id
                   AND decision = 'ACCEPTED') THEN
                RAISE EXCEPTION 'Docket requires an accepted complaint and its accepted decision'
                  USING ERRCODE = '23514', CONSTRAINT = 'ck_dockets_accepted_complaint';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER docket_acceptance BEFORE INSERT OR UPDATE ON case_mgmt.dockets
        FOR EACH ROW EXECUTE FUNCTION case_mgmt.validate_docket_acceptance()
    """)
    op.execute("""
        CREATE FUNCTION case_mgmt.protect_docket_complaint_status() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.status NOT IN ('ACCEPTED', 'DOCKET_CREATED') AND EXISTS
               (SELECT 1 FROM case_mgmt.dockets WHERE complaint_id = NEW.id) THEN
                RAISE EXCEPTION 'A complaint with a docket must remain accepted'
                  USING ERRCODE = '23514', CONSTRAINT = 'ck_complaints_docket_acceptance';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER complaint_docket_status BEFORE UPDATE OF status ON case_mgmt.complaints
        FOR EACH ROW EXECUTE FUNCTION case_mgmt.protect_docket_complaint_status()
    """)
    op.execute("""
        CREATE FUNCTION case_mgmt.validate_refusal_escalation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM case_mgmt.complaint_decisions
                WHERE id = NEW.complaint_decision_id AND complaint_id = NEW.complaint_id
                  AND decision = 'REFUSED') THEN
                RAISE EXCEPTION 'Escalation requires a refused decision for the same complaint'
                  USING ERRCODE = '23514', CONSTRAINT = 'ck_refusal_escalations_refused_decision';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER refusal_escalation_decision BEFORE INSERT OR UPDATE ON case_mgmt.refusal_escalations
        FOR EACH ROW EXECUTE FUNCTION case_mgmt.validate_refusal_escalation()
    """)
    # Restrict application writes to preserve historical content. The migration
    # owner retains maintenance access; there are no audit triggers.
    for name in ("complaint_decisions", "docket_approvals"):
        op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON case_mgmt.{name} FROM saps_api")
    for name in ("complaint_statements", "witness_statements"):
        op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON case_mgmt.{name} FROM saps_api")
        op.execute(f"GRANT UPDATE (is_current, signed_at, updated_at) ON case_mgmt.{name} TO saps_api")


def upgrade() -> None:
    """Upgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('refusal_reasons',
    sa.Column('code', sa.String(length=100), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('is_non_compliant', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('requires_escalation', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('requires_officer_notes', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_refusal_reasons')),
    schema='case_mgmt'
    )
    op.create_index(op.f('ix_refusal_reasons_code'), 'refusal_reasons', ['code'], unique=True, schema='case_mgmt')
    op.create_table('complaints',
    sa.Column('reference_number', sa.String(length=100), nullable=False),
    sa.Column('complainant_id', sa.UUID(), nullable=False),
    sa.Column('station_id', sa.UUID(), nullable=False),
    sa.Column('registered_by_officer_id', sa.UUID(), nullable=True),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=30), server_default=sa.text("'SUBMITTED'"), nullable=False),
    sa.Column('crime_category', sa.String(length=150), nullable=False),
    sa.Column('incident_description', sa.Text(), nullable=False),
    sa.Column('incident_occurred_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('incident_location', sa.String(length=255), nullable=False),
    sa.Column('incident_city', sa.String(length=150), nullable=True),
    sa.Column('incident_province', sa.String(length=100), nullable=False),
    sa.Column('submitted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('review_started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("channel <> 'IN_STATION' OR registered_by_officer_id IS NOT NULL", name=op.f('ck_complaints_in_station_officer')),
    sa.CheckConstraint("channel IN ('ONLINE', 'IN_STATION')", name=op.f('ck_complaints_channel')),
    sa.CheckConstraint("status IN ('SUBMITTED', 'UNDER_REVIEW', 'ACCEPTED', 'REFUSED', 'ESCALATED', 'DOCKET_CREATED')", name=op.f('ck_complaints_status')),
    sa.ForeignKeyConstraint(['complainant_id'], ['case_mgmt.complainants.id'], name=op.f('fk_complaints_complainant_id_complainants'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['registered_by_officer_id'], ['case_mgmt.officers.id'], name=op.f('fk_complaints_registered_by_officer_id_officers'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['station_id'], ['case_mgmt.stations.id'], name=op.f('fk_complaints_station_id_stations'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_complaints')),
    schema='case_mgmt'
    )
    op.create_index(op.f('ix_complaints_complainant_id'), 'complaints', ['complainant_id'], unique=False, schema='case_mgmt')
    op.create_index(op.f('ix_complaints_reference_number'), 'complaints', ['reference_number'], unique=True, schema='case_mgmt')
    op.create_index(op.f('ix_complaints_station_id'), 'complaints', ['station_id'], unique=False, schema='case_mgmt')
    op.create_index(op.f('ix_complaints_status'), 'complaints', ['status'], unique=False, schema='case_mgmt')
    op.create_index(op.f('ix_complaints_submitted_at'), 'complaints', ['submitted_at'], unique=False, schema='case_mgmt')
    op.create_table('complaint_decisions',
    sa.Column('complaint_id', sa.UUID(), nullable=False),
    sa.Column('decision', sa.String(length=20), nullable=False),
    sa.Column('decided_by_officer_id', sa.UUID(), nullable=False),
    sa.Column('refusal_reason_id', sa.UUID(), nullable=True),
    sa.Column('officer_notes', sa.Text(), nullable=True),
    sa.Column('decision_sequence', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.CheckConstraint("(decision = 'ACCEPTED' AND refusal_reason_id IS NULL) OR (decision = 'REFUSED' AND refusal_reason_id IS NOT NULL)", name=op.f('ck_complaint_decisions_refusal_reason')),
    sa.CheckConstraint("decision IN ('ACCEPTED', 'REFUSED')", name=op.f('ck_complaint_decisions_decision')),
    sa.CheckConstraint('decision_sequence > 0', name=op.f('ck_complaint_decisions_positive_sequence')),
    sa.ForeignKeyConstraint(['complaint_id'], ['case_mgmt.complaints.id'], name=op.f('fk_complaint_decisions_complaint_id_complaints'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['decided_by_officer_id'], ['case_mgmt.officers.id'], name=op.f('fk_complaint_decisions_decided_by_officer_id_officers'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['refusal_reason_id'], ['case_mgmt.refusal_reasons.id'], name=op.f('fk_complaint_decisions_refusal_reason_id_refusal_reasons'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_complaint_decisions')),
    sa.UniqueConstraint('complaint_id', 'decision_sequence', name=op.f('uq_complaint_decisions_complaint_id')),
    sa.UniqueConstraint('id', 'complaint_id', name='uq_complaint_decisions_id_complaint'),
    schema='case_mgmt'
    )
    op.create_index(op.f('ix_complaint_decisions_refusal_reason_id'), 'complaint_decisions', ['refusal_reason_id'], unique=False, schema='case_mgmt')
    op.create_table('complaint_statements',
    sa.Column('complaint_id', sa.UUID(), nullable=False),
    sa.Column('statement_text', sa.Text(), nullable=False),
    sa.Column('recorded_by_officer_id', sa.UUID(), nullable=True),
    sa.Column('statement_version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('is_current', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('signed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('statement_version > 0', name=op.f('ck_complaint_statements_positive_version')),
    sa.ForeignKeyConstraint(['complaint_id'], ['case_mgmt.complaints.id'], name=op.f('fk_complaint_statements_complaint_id_complaints'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['recorded_by_officer_id'], ['case_mgmt.officers.id'], name=op.f('fk_complaint_statements_recorded_by_officer_id_officers'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_complaint_statements')),
    sa.UniqueConstraint('complaint_id', 'statement_version', name=op.f('uq_complaint_statements_complaint_id')),
    schema='case_mgmt'
    )
    op.create_index('uq_complaint_statements_current', 'complaint_statements', ['complaint_id'], unique=True, schema='case_mgmt', postgresql_where=sa.text('is_current'))
    op.create_table('witnesses',
    sa.Column('complaint_id', sa.UUID(), nullable=False),
    sa.Column('first_name', sa.String(length=100), nullable=False),
    sa.Column('last_name', sa.String(length=100), nullable=False),
    sa.Column('phone_number', sa.String(length=30), nullable=True),
    sa.Column('email', sa.String(length=254), nullable=True),
    sa.Column('identity_number_encrypted', sa.Text(), nullable=True),
    sa.Column('identity_number_hash', sa.String(length=128), nullable=True),
    sa.Column('address', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['complaint_id'], ['case_mgmt.complaints.id'], name=op.f('fk_witnesses_complaint_id_complaints'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_witnesses')),
    schema='case_mgmt'
    )
    op.create_index(op.f('ix_witnesses_complaint_id'), 'witnesses', ['complaint_id'], unique=False, schema='case_mgmt')
    op.create_table('dockets',
    sa.Column('complaint_id', sa.UUID(), nullable=False),
    sa.Column('cas_number', sa.String(length=100), nullable=False),
    sa.Column('status', sa.String(length=30), server_default=sa.text("'PENDING_APPROVAL'"), nullable=False),
    sa.Column('created_from_decision_id', sa.Uuid(), nullable=False),
    sa.Column('opened_by_officer_id', sa.UUID(), nullable=False),
    sa.Column('opened_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('closure_reason', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('PENDING_APPROVAL', 'APPROVED', 'ACTIVE', 'ON_HOLD', 'CLOSED', 'ARCHIVED')", name=op.f('ck_dockets_status')),
    sa.ForeignKeyConstraint(['complaint_id'], ['case_mgmt.complaints.id'], name=op.f('fk_dockets_complaint_id_complaints'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['created_from_decision_id', 'complaint_id'], ['case_mgmt.complaint_decisions.id', 'case_mgmt.complaint_decisions.complaint_id'], name=op.f('fk_dockets_created_from_decision_id_complaint_decisions'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['opened_by_officer_id'], ['case_mgmt.officers.id'], name=op.f('fk_dockets_opened_by_officer_id_officers'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_dockets')),
    sa.UniqueConstraint('complaint_id', name=op.f('uq_dockets_complaint_id')),
    schema='case_mgmt'
    )
    op.create_index(op.f('ix_dockets_cas_number'), 'dockets', ['cas_number'], unique=True, schema='case_mgmt')
    op.create_index(op.f('ix_dockets_created_from_decision_id'), 'dockets', ['created_from_decision_id'], unique=False, schema='case_mgmt')
    op.create_index(op.f('ix_dockets_status'), 'dockets', ['status'], unique=False, schema='case_mgmt')
    op.create_table('refusal_escalations',
    sa.Column('complaint_id', sa.UUID(), nullable=False),
    sa.Column('complaint_decision_id', sa.Uuid(), nullable=False),
    sa.Column('target', sa.String(length=30), nullable=False),
    sa.Column('status', sa.String(length=20), server_default=sa.text("'OPEN'"), nullable=False),
    sa.Column('escalated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('acknowledged_by_user_id', sa.UUID(), nullable=True),
    sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolved_by_user_id', sa.UUID(), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolution_notes', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED')", name=op.f('ck_refusal_escalations_status')),
    sa.CheckConstraint("target IN ('STATION_COMMANDER', 'NCC')", name=op.f('ck_refusal_escalations_target')),
    sa.ForeignKeyConstraint(['acknowledged_by_user_id'], ['case_mgmt.users.id'], name=op.f('fk_refusal_escalations_acknowledged_by_user_id_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['complaint_decision_id', 'complaint_id'], ['case_mgmt.complaint_decisions.id', 'case_mgmt.complaint_decisions.complaint_id'], name=op.f('fk_refusal_escalations_complaint_decision_id_complaint_decisions'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['complaint_id'], ['case_mgmt.complaints.id'], name=op.f('fk_refusal_escalations_complaint_id_complaints'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['resolved_by_user_id'], ['case_mgmt.users.id'], name=op.f('fk_refusal_escalations_resolved_by_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_refusal_escalations')),
    sa.UniqueConstraint('complaint_decision_id', 'target', name=op.f('uq_refusal_escalations_complaint_decision_id')),
    schema='case_mgmt'
    )
    op.create_index(op.f('ix_refusal_escalations_complaint_id'), 'refusal_escalations', ['complaint_id'], unique=False, schema='case_mgmt')
    op.create_index(op.f('ix_refusal_escalations_status'), 'refusal_escalations', ['status'], unique=False, schema='case_mgmt')
    op.create_table('witness_statements',
    sa.Column('witness_id', sa.UUID(), nullable=False),
    sa.Column('statement_text', sa.Text(), nullable=False),
    sa.Column('recorded_by_officer_id', sa.UUID(), nullable=True),
    sa.Column('statement_version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('is_current', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('signed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('statement_version > 0', name=op.f('ck_witness_statements_positive_version')),
    sa.ForeignKeyConstraint(['recorded_by_officer_id'], ['case_mgmt.officers.id'], name=op.f('fk_witness_statements_recorded_by_officer_id_officers'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['witness_id'], ['case_mgmt.witnesses.id'], name=op.f('fk_witness_statements_witness_id_witnesses'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_witness_statements')),
    sa.UniqueConstraint('witness_id', 'statement_version', name=op.f('uq_witness_statements_witness_id')),
    schema='case_mgmt'
    )
    op.create_index('uq_witness_statements_current', 'witness_statements', ['witness_id'], unique=True, schema='case_mgmt', postgresql_where=sa.text('is_current'))
    op.create_table('docket_approvals',
    sa.Column('docket_id', sa.UUID(), nullable=False),
    sa.Column('decided_by_officer_id', sa.UUID(), nullable=False),
    sa.Column('decision', sa.String(length=30), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('decision_sequence', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.CheckConstraint("decision IN ('APPROVED', 'RETURNED_FOR_CORRECTION')", name=op.f('ck_docket_approvals_decision')),
    sa.CheckConstraint('decision_sequence > 0', name=op.f('ck_docket_approvals_positive_sequence')),
    sa.ForeignKeyConstraint(['decided_by_officer_id'], ['case_mgmt.officers.id'], name=op.f('fk_docket_approvals_decided_by_officer_id_officers'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['docket_id'], ['case_mgmt.dockets.id'], name=op.f('fk_docket_approvals_docket_id_dockets'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_docket_approvals')),
    sa.UniqueConstraint('docket_id', 'decision_sequence', name=op.f('uq_docket_approvals_docket_id')),
    schema='case_mgmt'
    )
    op.bulk_insert(refusal_seed_table(), REFUSAL_SEEDS)
    create_integrity_guards()


def downgrade() -> None:
    """Downgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('docket_approvals', schema='case_mgmt')
    op.drop_index('uq_witness_statements_current', table_name='witness_statements', schema='case_mgmt', postgresql_where=sa.text('is_current'))
    op.drop_table('witness_statements', schema='case_mgmt')
    op.drop_index(op.f('ix_refusal_escalations_status'), table_name='refusal_escalations', schema='case_mgmt')
    op.drop_index(op.f('ix_refusal_escalations_complaint_id'), table_name='refusal_escalations', schema='case_mgmt')
    op.drop_table('refusal_escalations', schema='case_mgmt')
    op.drop_index(op.f('ix_dockets_status'), table_name='dockets', schema='case_mgmt')
    op.drop_index(op.f('ix_dockets_created_from_decision_id'), table_name='dockets', schema='case_mgmt')
    op.drop_index(op.f('ix_dockets_cas_number'), table_name='dockets', schema='case_mgmt')
    op.drop_table('dockets', schema='case_mgmt')
    op.drop_index(op.f('ix_witnesses_complaint_id'), table_name='witnesses', schema='case_mgmt')
    op.drop_table('witnesses', schema='case_mgmt')
    op.drop_index('uq_complaint_statements_current', table_name='complaint_statements', schema='case_mgmt', postgresql_where=sa.text('is_current'))
    op.drop_table('complaint_statements', schema='case_mgmt')
    op.drop_index(op.f('ix_complaint_decisions_refusal_reason_id'), table_name='complaint_decisions', schema='case_mgmt')
    op.drop_table('complaint_decisions', schema='case_mgmt')
    op.drop_index(op.f('ix_complaints_submitted_at'), table_name='complaints', schema='case_mgmt')
    op.drop_index(op.f('ix_complaints_status'), table_name='complaints', schema='case_mgmt')
    op.drop_index(op.f('ix_complaints_station_id'), table_name='complaints', schema='case_mgmt')
    op.drop_index(op.f('ix_complaints_reference_number'), table_name='complaints', schema='case_mgmt')
    op.drop_index(op.f('ix_complaints_complainant_id'), table_name='complaints', schema='case_mgmt')
    op.drop_table('complaints', schema='case_mgmt')
    reasons = refusal_seed_table()
    op.execute(reasons.delete().where(reasons.c.id.in_([row["id"] for row in REFUSAL_SEEDS])))
    op.drop_index(op.f('ix_refusal_reasons_code'), table_name='refusal_reasons', schema='case_mgmt')
    op.drop_table('refusal_reasons', schema='case_mgmt')
    op.execute("DROP FUNCTION case_mgmt.validate_refusal_escalation()")
    op.execute("DROP FUNCTION case_mgmt.protect_docket_complaint_status()")
    op.execute("DROP FUNCTION case_mgmt.validate_docket_acceptance()")
    # ### end Alembic commands ###

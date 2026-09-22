"""create foundational identity and access tables

Revision ID: 9d6a130b9b0d
Revises: 
Create Date: 2026-09-21 05:51:14.184575

"""
from typing import Sequence, Union
from uuid import UUID

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '9d6a130b9b0d'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Stable identifiers: never regenerate these when replaying the migration.
ROLE_SEEDS = [
    {"id": UUID("10000000-0000-4000-8000-000000000001"), "code": 'COMPLAINANT', "name": 'Complainant'},
    {"id": UUID("10000000-0000-4000-8000-000000000002"), "code": 'CHARGE_OFFICER', "name": 'Charge Officer'},
    {"id": UUID("10000000-0000-4000-8000-000000000003"), "code": 'STATION_COMMANDER', "name": 'Captain / Station Commander'},
    {"id": UUID("10000000-0000-4000-8000-000000000004"), "code": 'INVESTIGATING_OFFICER', "name": 'Investigating Officer'},
    {"id": UUID("10000000-0000-4000-8000-000000000005"), "code": 'SYSTEM_ADMINISTRATOR', "name": 'System Administrator'},
    {"id": UUID("10000000-0000-4000-8000-000000000006"), "code": 'SAPS_MANAGEMENT', "name": 'SAPS Management'},
    {"id": UUID("10000000-0000-4000-8000-000000000007"), "code": 'NCC_OFFICER', "name": 'National Complaints Call Centre Officer'},
]

PERMISSION_SEEDS = [
    {"id": UUID("20000000-0000-4000-8000-000000000001"), "code": 'complaint.submit', "name": 'Complaint Submit'},
    {"id": UUID("20000000-0000-4000-8000-000000000002"), "code": 'complaint.register', "name": 'Complaint Register'},
    {"id": UUID("20000000-0000-4000-8000-000000000003"), "code": 'complaint.view_own', "name": 'Complaint View Own'},
    {"id": UUID("20000000-0000-4000-8000-000000000004"), "code": 'complaint.view_station', "name": 'Complaint View Station'},
    {"id": UUID("20000000-0000-4000-8000-000000000005"), "code": 'complaint.decide', "name": 'Complaint Decide'},
    {"id": UUID("20000000-0000-4000-8000-000000000006"), "code": 'refusal.record', "name": 'Refusal Record'},
    {"id": UUID("20000000-0000-4000-8000-000000000007"), "code": 'refusal.escalation.view', "name": 'Refusal Escalation View'},
    {"id": UUID("20000000-0000-4000-8000-000000000008"), "code": 'confirmation.download', "name": 'Confirmation Download'},
    {"id": UUID("20000000-0000-4000-8000-000000000009"), "code": 'docket.approve', "name": 'Docket Approve'},
    {"id": UUID("20000000-0000-4000-8000-000000000010"), "code": 'docket.assign', "name": 'Docket Assign'},
    {"id": UUID("20000000-0000-4000-8000-000000000011"), "code": 'docket.view_assigned', "name": 'Docket View Assigned'},
    {"id": UUID("20000000-0000-4000-8000-000000000012"), "code": 'case.track_own', "name": 'Case Track Own'},
    {"id": UUID("20000000-0000-4000-8000-000000000013"), "code": 'case.update_status', "name": 'Case Update Status'},
    {"id": UUID("20000000-0000-4000-8000-000000000014"), "code": 'case.add_note', "name": 'Case Add Note'},
    {"id": UUID("20000000-0000-4000-8000-000000000015"), "code": 'case.close', "name": 'Case Close'},
    {"id": UUID("20000000-0000-4000-8000-000000000016"), "code": 'evidence.manage', "name": 'Evidence Manage'},
    {"id": UUID("20000000-0000-4000-8000-000000000017"), "code": 'evidence.view_custody', "name": 'Evidence View Custody'},
    {"id": UUID("20000000-0000-4000-8000-000000000018"), "code": 'feedback.provide', "name": 'Feedback Provide'},
    {"id": UUID("20000000-0000-4000-8000-000000000019"), "code": 'audit.view_station', "name": 'Audit View Station'},
    {"id": UUID("20000000-0000-4000-8000-000000000020"), "code": 'audit.view_all', "name": 'Audit View All'},
    {"id": UUID("20000000-0000-4000-8000-000000000021"), "code": 'dashboard.view_station', "name": 'Dashboard View Station'},
    {"id": UUID("20000000-0000-4000-8000-000000000022"), "code": 'dashboard.view_all', "name": 'Dashboard View All'},
    {"id": UUID("20000000-0000-4000-8000-000000000023"), "code": 'alert.view', "name": 'Alert View'},
    {"id": UUID("20000000-0000-4000-8000-000000000024"), "code": 'user.manage', "name": 'User Manage'},
    {"id": UUID("20000000-0000-4000-8000-000000000025"), "code": 'role.manage', "name": 'Role Manage'},
    {"id": UUID("20000000-0000-4000-8000-000000000026"), "code": 'permission.manage', "name": 'Permission Manage'},
    {"id": UUID("20000000-0000-4000-8000-000000000027"), "code": 'station.manage', "name": 'Station Manage'},
]


def seed_table(name):
    return sa.table(
        name, sa.column("id", sa.UUID()), sa.column("code", sa.String()),
        sa.column("name", sa.String()), schema="case_mgmt",
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('permissions',
    sa.Column('code', sa.String(length=100), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_permissions')),
    schema='case_mgmt'
    )
    op.create_index(op.f('ix_permissions_code'), 'permissions', ['code'], unique=True, schema='case_mgmt')
    op.create_table('roles',
    sa.Column('code', sa.String(length=100), nullable=False),
    sa.Column('name', sa.String(length=150), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('is_system_role', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_roles')),
    sa.UniqueConstraint('name', name=op.f('uq_roles_name')),
    schema='case_mgmt'
    )
    op.create_index(op.f('ix_roles_code'), 'roles', ['code'], unique=True, schema='case_mgmt')
    op.create_table('stations',
    sa.Column('station_code', sa.String(length=50), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('province', sa.String(length=100), nullable=False),
    sa.Column('district', sa.String(length=150), nullable=True),
    sa.Column('address_line_1', sa.String(length=255), nullable=True),
    sa.Column('address_line_2', sa.String(length=255), nullable=True),
    sa.Column('city', sa.String(length=150), nullable=True),
    sa.Column('postal_code', sa.String(length=20), nullable=True),
    sa.Column('phone_number', sa.String(length=30), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_stations')),
    schema='case_mgmt'
    )
    op.create_index(op.f('ix_stations_name'), 'stations', ['name'], unique=False, schema='case_mgmt')
    op.create_index(op.f('ix_stations_province'), 'stations', ['province'], unique=False, schema='case_mgmt')
    op.create_index(op.f('ix_stations_station_code'), 'stations', ['station_code'], unique=True, schema='case_mgmt')
    op.create_table('users',
    sa.Column('username', sa.String(length=100), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('phone_number', sa.String(length=30), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('is_verified', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('mfa_enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('failed_login_attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    schema='case_mgmt'
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True, schema='case_mgmt')
    op.create_index(op.f('ix_users_username'), 'users', ['username'], unique=True, schema='case_mgmt')
    op.create_table('complainants',
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('first_name', sa.String(length=100), nullable=False),
    sa.Column('last_name', sa.String(length=100), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=True),
    sa.Column('phone_number', sa.String(length=30), nullable=False),
    sa.Column('preferred_contact_method', sa.String(length=10), nullable=False),
    sa.Column('identity_type', sa.String(length=50), nullable=True),
    sa.Column('identity_number_encrypted', sa.Text(), nullable=True),
    sa.Column('identity_number_hash', sa.String(length=128), nullable=True),
    sa.Column('address_line_1', sa.String(length=255), nullable=True),
    sa.Column('address_line_2', sa.String(length=255), nullable=True),
    sa.Column('city', sa.String(length=150), nullable=True),
    sa.Column('province', sa.String(length=100), nullable=True),
    sa.Column('postal_code', sa.String(length=20), nullable=True),
    sa.Column('privacy_notice_version', sa.String(length=50), nullable=True),
    sa.Column('consent_recorded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("preferred_contact_method IN ('SMS', 'EMAIL', 'PHONE')", name=op.f('ck_complainants_preferred_contact_method')),
    sa.ForeignKeyConstraint(['user_id'], ['case_mgmt.users.id'], name=op.f('fk_complainants_user_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_complainants')),
    sa.UniqueConstraint('identity_number_hash', name=op.f('uq_complainants_identity_number_hash')),
    sa.UniqueConstraint('user_id', name=op.f('uq_complainants_user_id')),
    schema='case_mgmt'
    )
    op.create_table('officers',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('station_id', sa.UUID(), nullable=False),
    sa.Column('service_number', sa.String(length=50), nullable=False),
    sa.Column('rank', sa.String(length=100), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['station_id'], ['case_mgmt.stations.id'], name=op.f('fk_officers_station_id_stations'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['case_mgmt.users.id'], name=op.f('fk_officers_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_officers')),
    sa.UniqueConstraint('user_id', name=op.f('uq_officers_user_id')),
    schema='case_mgmt'
    )
    op.create_index(op.f('ix_officers_service_number'), 'officers', ['service_number'], unique=True, schema='case_mgmt')
    op.create_index(op.f('ix_officers_station_id'), 'officers', ['station_id'], unique=False, schema='case_mgmt')
    op.create_table('role_permissions',
    sa.Column('role_id', sa.UUID(), nullable=False),
    sa.Column('permission_id', sa.UUID(), nullable=False),
    sa.Column('granted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['permission_id'], ['case_mgmt.permissions.id'], name=op.f('fk_role_permissions_permission_id_permissions'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['role_id'], ['case_mgmt.roles.id'], name=op.f('fk_role_permissions_role_id_roles'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('role_id', 'permission_id', name=op.f('pk_role_permissions')),
    schema='case_mgmt'
    )
    op.create_table('user_roles',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('role_id', sa.UUID(), nullable=False),
    sa.Column('assigned_by_user_id', sa.UUID(), nullable=True),
    sa.Column('assigned_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['assigned_by_user_id'], ['case_mgmt.users.id'], name=op.f('fk_user_roles_assigned_by_user_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['role_id'], ['case_mgmt.roles.id'], name=op.f('fk_user_roles_role_id_roles'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['case_mgmt.users.id'], name=op.f('fk_user_roles_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'role_id', name=op.f('pk_user_roles')),
    schema='case_mgmt'
    )
    op.bulk_insert(seed_table("roles"), ROLE_SEEDS)
    op.bulk_insert(seed_table("permissions"), PERMISSION_SEEDS)


def downgrade() -> None:
    """Downgrade schema, removing seeds before dropping their tables."""
    for name, records in (("roles", ROLE_SEEDS), ("permissions", PERMISSION_SEEDS)):
        table = seed_table(name)
        op.execute(table.delete().where(table.c.id.in_([row["id"] for row in records])))
    op.drop_table('user_roles', schema='case_mgmt')
    op.drop_table('role_permissions', schema='case_mgmt')
    op.drop_index(op.f('ix_officers_station_id'), table_name='officers', schema='case_mgmt')
    op.drop_index(op.f('ix_officers_service_number'), table_name='officers', schema='case_mgmt')
    op.drop_table('officers', schema='case_mgmt')
    op.drop_table('complainants', schema='case_mgmt')
    op.drop_index(op.f('ix_users_username'), table_name='users', schema='case_mgmt')
    op.drop_index(op.f('ix_users_email'), table_name='users', schema='case_mgmt')
    op.drop_table('users', schema='case_mgmt')
    op.drop_index(op.f('ix_stations_station_code'), table_name='stations', schema='case_mgmt')
    op.drop_index(op.f('ix_stations_province'), table_name='stations', schema='case_mgmt')
    op.drop_index(op.f('ix_stations_name'), table_name='stations', schema='case_mgmt')
    op.drop_table('stations', schema='case_mgmt')
    op.drop_index(op.f('ix_roles_code'), table_name='roles', schema='case_mgmt')
    op.drop_table('roles', schema='case_mgmt')
    op.drop_index(op.f('ix_permissions_code'), table_name='permissions', schema='case_mgmt')
    op.drop_table('permissions', schema='case_mgmt')

"""Limit the existing administrator role to accounts and audit, not case dashboards."""
from alembic import op

revision='e712ac340a01'
down_revision='b360beaef541'
branch_labels=None
depends_on=None


def upgrade():
    op.execute("DELETE FROM case_mgmt.role_permissions WHERE role_id=(SELECT id FROM case_mgmt.roles WHERE code='SYSTEM_ADMINISTRATOR') AND permission_id IN (SELECT id FROM case_mgmt.permissions WHERE code IN ('role.manage','permission.manage','station.manage','dashboard.view_all','alert.view'))")


def downgrade():
    op.execute("INSERT INTO case_mgmt.role_permissions(role_id,permission_id) SELECT r.id,p.id FROM case_mgmt.roles r CROSS JOIN case_mgmt.permissions p WHERE r.code='SYSTEM_ADMINISTRATOR' AND p.code IN ('role.manage','permission.manage','station.manage','dashboard.view_all','alert.view') ON CONFLICT DO NOTHING")

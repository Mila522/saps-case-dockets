"""Read-only schema review; outputs metadata only, never record values or URLs."""
import json
import re

from sqlalchemy import CheckConstraint, LargeBinary, inspect
from sqlalchemy.orm import configure_mappers

from app.db import models  # noqa: F401 -- register all tables
from app.db.base import Base
from app.db.session import engine
from app.modules.dockets.models import Docket
from app.modules.investigations.models import DocketStatusHistory


def review():
    configure_mappers()
    result = {"tables": len(Base.metadata.tables), "errors": [], "foreign_key_index_candidates": [], "overlapping_index_candidates": []}
    with engine.connect() as conn:
        ins = inspect(conn)
        expected = {t.name for t in Base.metadata.tables.values()} | {"alembic_version"}
        if set(ins.get_table_names(schema="case_mgmt")) != expected:
            result["errors"].append("Live tables differ from model inventory")
        result['other_application_schemas'] = {s: ins.get_table_names(schema=s) for s in ins.get_schema_names()
            if s not in ('case_mgmt', 'pg_catalog', 'information_schema') and ins.get_table_names(schema=s)}
        for table in Base.metadata.sorted_tables:
            if not table.primary_key.columns:
                result['errors'].append(table.name + ': missing primary key')
            columns = {c['name']: c for c in ins.get_columns(table.name, schema='case_mgmt')}
            checks = {c['name'] for c in ins.get_check_constraints(table.name, schema='case_mgmt')}
            expected_checks = {conn.dialect.identifier_preparer.format_constraint(c) for c in table.constraints if isinstance(c, CheckConstraint)}
            if checks != expected_checks:
                result['errors'].append(table.name + ': check constraints differ')
            for column in table.columns:
                if columns[column.name]['nullable'] != column.nullable:
                    result['errors'].append(table.name + '.' + column.name + ': nullability differs')
                if column.server_default is not None and columns[column.name]['default'] is None:
                    result['errors'].append(table.name + '.' + column.name + ': missing server default')
                if isinstance(column.type, LargeBinary) or column.name in ('password', 'identity_number', 'public_url'):
                    result['errors'].append(table.name + '.' + column.name + ': sensitive storage review required')
            indexes = ins.get_indexes(table.name, schema='case_mgmt')
            full_keys = [tuple(i['column_names']) for i in indexes if not i.get('dialect_options', {}).get('postgresql_where')]
            full_keys.append(tuple(c.name for c in table.primary_key))
            for fk in table.foreign_key_constraints:
                fk_cols = tuple(c.name for c in fk.columns)
                # Any leading FK column provides a useful candidate lookup; a
                # composite FK need not have an identical composite index.
                if not any(k and k[0] in fk_cols for k in full_keys):
                    result['foreign_key_index_candidates'].append(table.name + '(' + ', '.join(fk_cols) + ')')
            seen = set()
            for index in indexes:
                # Functional indexes have None column names; distinguish their
                # SQL expressions instead of treating lower(email/username) as duplicates.
                key = (tuple(index.get('expressions') or index['column_names']), index.get('dialect_options', {}).get('postgresql_where'))
                if key in seen:
                    result['errors'].append(table.name + ': exact duplicate index ' + index['name'])
                seen.add(key)
                if not index['unique'] and not key[1] and any(k[:len(key[0])] == key[0] and len(k) > len(key[0]) for k in full_keys):
                    result['overlapping_index_candidates'].append(index['name'])
    current = next(c for c in Docket.__table__.constraints if isinstance(c, CheckConstraint))
    allowed = set(re.findall("'([^']+)'", str(current.sqltext)))
    for c in DocketStatusHistory.__table__.constraints:
        if isinstance(c, CheckConstraint) and c.name.endswith(('_from_status', '_to_status')):
            if set(re.findall("'([^']+)'", str(c.sqltext))) != allowed:
                result['errors'].append('Docket status/history allowed values differ')
    return result


if __name__ == '__main__':
    report = review()
    print(json.dumps(report, indent=2))
    if report['errors']:
        raise SystemExit(1)

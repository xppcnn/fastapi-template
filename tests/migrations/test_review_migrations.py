import runpy
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models import Base


def test_new_migrations_round_trip_and_match_models():
    root = Path(__file__).resolve().parents[2] / "alembic" / "versions"
    recovery = runpy.run_path(str(root / "20260908_0008_extraction_recovery.py"))
    reviews = runpy.run_path(str(root / "20260908_0009_review_snapshots.py"))
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as conn:
            Base.metadata.create_all(conn)
            Base.metadata.tables["review_run_rules"].drop(conn)
            Base.metadata.tables["review_runs"].drop(conn)

            def include_object(obj, name, type_, reflected, compare_to):
                if type_ == "table":
                    return name in {
                        "rule_extraction_runs",
                        "review_runs",
                        "review_run_rules",
                    }
                # 仓库既有迁移移除了 SQLAlchemy 隐式 Enum CHECK，create_all 测试库保留它。
                return not (
                    type_ == "check_constraint" and name == "rule_extraction_run_status"
                )

            ctx = MigrationContext.configure(
                conn, opts={"include_object": include_object}
            )
            with Operations.context(ctx):
                recovery["downgrade"]()
                old = sa.Table(
                    "rule_extraction_runs", sa.MetaData(), autoload_with=conn
                )
                public_id = uuid4()
                conn.execute(
                    old.insert().values(
                        public_id=public_id.hex,
                        organization_id=1,
                        project_id=1,
                        tender_version_id=1,
                        status="queued",
                        total_batches=1,
                        completed_batches=0,
                        failed_batches=0,
                    )
                )
                recovery["upgrade"]()
                token = conn.scalar(
                    sa.text("SELECT execution_token FROM rule_extraction_runs")
                )
                assert token == public_id.hex
                reviews["upgrade"]()
                assert compare_metadata(ctx, Base.metadata) == []
                reviews["downgrade"]()
                recovery["downgrade"]()
                assert "review_runs" not in sa.inspect(conn).get_table_names()
                assert "execution_token" not in {
                    c["name"]
                    for c in sa.inspect(conn).get_columns("rule_extraction_runs")
                }
                recovery["upgrade"]()
                reviews["upgrade"]()
                assert compare_metadata(ctx, Base.metadata) == []
    finally:
        engine.dispose()

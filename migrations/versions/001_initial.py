from alembic import op

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "\nCREATE TABLE budgets (\n\tid VARCHAR(100) NOT NULL, \n\treserved NUMERIC(12, 6) NOT NULL, \n\tPRIMARY KEY (id)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE document_versions (\n\tid SERIAL NOT NULL, \n\turl VARCHAR(1000) NOT NULL, \n\tcontent_hash VARCHAR(64) NOT NULL, \n\tfetched_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\ttext TEXT NOT NULL, \n\tPRIMARY KEY (id)\n)\n\n"
    )
    op.execute("CREATE INDEX ix_document_versions_url ON document_versions (url)")
    op.execute(
        "\nCREATE TABLE documents (\n\tid SERIAL NOT NULL, \n\turl VARCHAR(1000) NOT NULL, \n\ttitle VARCHAR(300) NOT NULL, \n\ttext TEXT NOT NULL, \n\tcontent_hash VARCHAR(64) NOT NULL, \n\tfetched_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tttl_seconds INTEGER NOT NULL, \n\tsearch TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', title || ' ' || text)) STORED NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (url)\n)\n\n"
    )
    op.execute("CREATE INDEX documents_search_idx ON documents USING gin (search)")
    op.execute(
        "\nCREATE TABLE outcomes (\n\tid VARCHAR(36) NOT NULL, \n\tstatus VARCHAR(20) NOT NULL, \n\telapsed_ms INTEGER NOT NULL, \n\tcache_hit BOOLEAN NOT NULL, \n\tsource_ids JSON NOT NULL, \n\tproviders JSON NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE ratings (\n\tanswer_id VARCHAR(36) NOT NULL, \n\tuseful BOOLEAN NOT NULL, \n\tPRIMARY KEY (answer_id)\n)\n\n"
    )
    op.execute(
        "\nCREATE TABLE reservations (\n\tid VARCHAR(36) NOT NULL, \n\tbudget_id VARCHAR(100) NOT NULL, \n\tpurpose VARCHAR(40) NOT NULL, \n\tamount NUMERIC(12, 6) NOT NULL, \n\tusage JSON NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id)\n)\n\n"
    )
    op.execute("CREATE INDEX ix_reservations_budget_id ON reservations (budget_id)")


def downgrade():
    op.drop_table("reservations")
    op.drop_table("ratings")
    op.drop_table("outcomes")
    op.drop_table("documents")
    op.drop_table("document_versions")
    op.drop_table("budgets")

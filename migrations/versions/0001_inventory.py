"""Inventory, documents and immutable journal.

Revision ID: 0001
"""

from alembic import op

SCHEMA = [
    """CREATE TABLE stores (
	id VARCHAR(36) NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	PRIMARY KEY (id)
)""",
    """CREATE TABLE idempotency (
	id VARCHAR(36) NOT NULL, 
	store_id VARCHAR(36) NOT NULL, 
	key VARCHAR(160) NOT NULL, 
	fingerprint VARCHAR(64) NOT NULL, 
	response JSONB NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (store_id, key), 
	FOREIGN KEY(store_id) REFERENCES stores (id)
)""",
    """CREATE INDEX ix_idempotency_store_id ON idempotency (store_id)""",
    """CREATE TABLE products (
	id VARCHAR(36) NOT NULL, 
	store_id VARCHAR(36) NOT NULL, 
	sku VARCHAR(80) NOT NULL, 
	name VARCHAR(160) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (store_id, sku), 
	FOREIGN KEY(store_id) REFERENCES stores (id)
)""",
    """CREATE INDEX ix_products_store_id ON products (store_id)""",
    """CREATE TABLE warehouses (
	id VARCHAR(36) NOT NULL, 
	store_id VARCHAR(36) NOT NULL, 
	name VARCHAR(80) NOT NULL, 
	archived BOOLEAN NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (store_id, name), 
	FOREIGN KEY(store_id) REFERENCES stores (id)
)""",
    """CREATE INDEX ix_warehouses_store_id ON warehouses (store_id)""",
    """CREATE TABLE documents (
	id VARCHAR(36) NOT NULL, 
	store_id VARCHAR(36) NOT NULL, 
	product_id VARCHAR(36) NOT NULL, 
	kind VARCHAR(20) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	quantity INTEGER NOT NULL, 
	source_id VARCHAR(36), 
	destination_id VARCHAR(36), 
	source_name VARCHAR(120) NOT NULL, 
	destination_name VARCHAR(120) NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT document_positive_quantity CHECK (quantity > 0), 
	FOREIGN KEY(store_id) REFERENCES stores (id), 
	FOREIGN KEY(product_id) REFERENCES products (id), 
	FOREIGN KEY(source_id) REFERENCES warehouses (id), 
	FOREIGN KEY(destination_id) REFERENCES warehouses (id)
)""",
    """CREATE INDEX ix_documents_created_at ON documents (created_at)""",
    """CREATE INDEX ix_documents_product_id ON documents (product_id)""",
    """CREATE INDEX ix_documents_store_id ON documents (store_id)""",
    """CREATE TABLE positions (
	id VARCHAR(36) NOT NULL, 
	store_id VARCHAR(36) NOT NULL, 
	product_id VARCHAR(36) NOT NULL, 
	account VARCHAR(64) NOT NULL, 
	warehouse_id VARCHAR(36), 
	quantity INTEGER NOT NULL, 
	reserved INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (store_id, product_id, account), 
	CONSTRAINT quantity_nonnegative CHECK (quantity >= 0), 
	CONSTRAINT valid_reserve CHECK (reserved >= 0 AND reserved <= quantity), 
	CONSTRAINT reserve_only_ff CHECK (warehouse_id IS NOT NULL OR reserved = 0), 
	FOREIGN KEY(store_id) REFERENCES stores (id), 
	FOREIGN KEY(product_id) REFERENCES products (id), 
	FOREIGN KEY(warehouse_id) REFERENCES warehouses (id)
)""",
    """CREATE INDEX ix_positions_product_id ON positions (product_id)""",
    """CREATE INDEX ix_positions_store_id ON positions (store_id)""",
    """CREATE TABLE events (
	id VARCHAR(36) NOT NULL, 
	store_id VARCHAR(36) NOT NULL, 
	document_id VARCHAR(36), 
	product_id VARCHAR(36), 
	action VARCHAR(40) NOT NULL, 
	quantity INTEGER NOT NULL, 
	source VARCHAR(120) NOT NULL, 
	destination VARCHAR(120) NOT NULL, 
	details JSONB NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(store_id) REFERENCES stores (id), 
	FOREIGN KEY(document_id) REFERENCES documents (id), 
	FOREIGN KEY(product_id) REFERENCES products (id)
)""",
    """CREATE INDEX ix_events_created_at ON events (created_at)""",
    """CREATE INDEX ix_events_store_id ON events (store_id)""",
    """CREATE TABLE postings (
	id VARCHAR(36) NOT NULL, 
	event_id VARCHAR(36) NOT NULL, 
	position_id VARCHAR(36) NOT NULL, 
	quantity_delta INTEGER NOT NULL, 
	reserved_delta INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(event_id) REFERENCES events (id), 
	FOREIGN KEY(position_id) REFERENCES positions (id)
)""",
    """CREATE INDEX ix_postings_event_id ON postings (event_id)""",
    """CREATE INDEX ix_postings_position_id ON postings (position_id)""",
]
TABLES = [
    "postings",
    "events",
    "positions",
    "documents",
    "warehouses",
    "products",
    "idempotency",
    "stores",
]

revision = "0001"
down_revision = None


def upgrade():
    for statement in SCHEMA:
        op.execute(statement)
    op.execute("""
        CREATE FUNCTION protect_journal() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'Inventory journal is append-only';
        END; $$;
        CREATE TRIGGER events_append_only BEFORE UPDATE OR DELETE ON events
          FOR EACH ROW EXECUTE FUNCTION protect_journal();
        CREATE TRIGGER postings_append_only BEFORE UPDATE OR DELETE ON postings
          FOR EACH ROW EXECUTE FUNCTION protect_journal();
    """)


def downgrade():
    op.execute("DROP FUNCTION protect_journal() CASCADE")
    for table in TABLES:
        op.drop_table(table)

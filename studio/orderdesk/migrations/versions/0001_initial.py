from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("customers", sa.Column("id", sa.Integer, primary_key=True),
                    sa.Column("name", sa.String(120), nullable=False), sa.Column("email", sa.String(254), nullable=False))
    op.create_table("products", sa.Column("id", sa.Integer, primary_key=True),
                    sa.Column("sku", sa.String(64), nullable=False, unique=True), sa.Column("name", sa.String(120), nullable=False))
    op.create_table("orders", sa.Column("id", sa.Integer, primary_key=True),
                    sa.Column("customer_id", sa.Integer, sa.ForeignKey("customers.id"), nullable=False),
                    sa.Column("status", sa.String(16), nullable=False), sa.Column("currency", sa.String(3), nullable=False),
                    sa.Column("created_at", sa.String(40), nullable=False), sa.Column("version", sa.Integer, nullable=False),
                    sa.CheckConstraint("status IN ('draft','confirmed','completed','cancelled')"),
                    sa.CheckConstraint("currency IN ('RUB','USD','EUR')"), sa.CheckConstraint("version >= 1"))
    op.create_table("order_items", sa.Column("id", sa.Integer, primary_key=True),
                    sa.Column("order_id", sa.Integer, sa.ForeignKey("orders.id"), nullable=False),
                    sa.Column("product_id", sa.Integer, sa.ForeignKey("products.id"), nullable=False),
                    sa.Column("quantity", sa.Integer, nullable=False), sa.Column("price_minor", sa.BigInteger, nullable=False),
                    sa.CheckConstraint("quantity BETWEEN 1 AND 1000000"),
                    sa.CheckConstraint("price_minor BETWEEN 0 AND 1000000000"), sa.UniqueConstraint("order_id", "product_id"))
    op.create_table("audit_events", sa.Column("id", sa.Integer, primary_key=True),
                    sa.Column("order_id", sa.Integer, sa.ForeignKey("orders.id"), nullable=False),
                    sa.Column("action", sa.String(80), nullable=False), sa.Column("timestamp", sa.String(40), nullable=False))
    op.create_table("idempotency", sa.Column("key", sa.String(128), primary_key=True),
                    sa.Column("request_json", sa.Text, nullable=False), sa.Column("response_json", sa.Text, nullable=False))
    op.create_index("ix_items_order", "order_items", ["order_id"])
    op.create_index("ix_events_order", "audit_events", ["order_id"])


def downgrade():
    for name in ("idempotency", "audit_events", "order_items", "orders", "products", "customers"):
        op.drop_table(name)

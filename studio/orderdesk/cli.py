import argparse
from pathlib import Path

from sqlalchemy import text

from .db import make_engine, migrate


def seed(path):
    engine = make_engine(path)
    try:
        migrate(engine)
        with engine.begin() as connection:
            if connection.execute(text("SELECT count(*) FROM customers")).scalar_one() or connection.execute(text("SELECT count(*) FROM products")).scalar_one():
                print("Seed skipped: database already contains customers or products")
                return
            connection.execute(text("INSERT INTO customers(name,email) VALUES ('Synthetic Retail','demo@example.com')"))
            connection.execute(text("INSERT INTO products(sku,name) VALUES ('DEMO-01','Synthetic cable'),('DEMO-02','Demo adapter')"))
        print("Created synthetic customer 1 and products 1, 2")
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="Order Desk local API")
    parser.add_argument("command", choices=["serve", "migrate", "seed"], nargs="?", default="serve")
    parser.add_argument("--db", default="data/orderdesk.sqlite3")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    if args.command == "seed":
        seed(args.db)
    elif args.command == "migrate":
        engine = make_engine(args.db)
        try:
            migrate(engine)
            print("Database schema upgraded to head")
        finally:
            engine.dispose()
    else:
        import uvicorn
        from .api import create_app

        print(f"Open http://127.0.0.1:{args.port}/docs; database: {Path(args.db).resolve()}", flush=True)
        uvicorn.run(create_app(args.db), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()

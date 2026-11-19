import argparse
import json
import sys
from zipfile import BadZipFile
from .pipeline import export_report


def main():
    parser = argparse.ArgumentParser(description="Clean and reconcile sales tables locally")
    parser.add_argument("orders")
    parser.add_argument("catalog")
    parser.add_argument("--output", default="reports")
    args = parser.parse_args()
    try:
        summary = export_report(args.orders, args.catalog, args.output)
    except (ValueError, OSError, BadZipFile) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

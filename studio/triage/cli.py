import argparse
import json
from pathlib import Path

from .rules import CATEGORIES, RULE_VERSION, classify, redact
from .service import Inbox


def evaluate(path):
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("Evaluation requires a non-empty JSON array")
    confusion = {expected: {predicted: 0 for predicted in CATEGORIES} for expected in CATEGORIES}
    results = []
    for row in rows:
        if row["expected"] not in CATEGORIES:
            raise ValueError("Invalid expected category")
        predicted = classify(redact(row["body"]))["category"]
        confusion[row["expected"]][predicted] += 1
        results.append({"id": row["id"], "expected": row["expected"], "predicted": predicted})
    correct = sum(row["expected"] == row["predicted"] for row in results)
    return {"engine": RULE_VERSION, "dataset": Path(path).name, "synthetic": True, "samples": len(results),
            "correct": correct, "accuracy": correct / len(results), "confusion_matrix": confusion,
            "manual_review_count": sum(row["predicted"] == "unknown" for row in results),
            "limitations": "Small author-labelled synthetic set, not independent validation or client accuracy. Confidence scores are not calibrated probabilities.",
            "results": results}


def main():
    parser = argparse.ArgumentParser(description="Local Inbox Triage, no outbound delivery")
    parser.add_argument("command", choices=["serve", "seed", "import", "export", "evaluate"], nargs="?", default="serve")
    parser.add_argument("--db", default="data/inbox.sqlite3")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--input")
    parser.add_argument("--output", default="reports/triage-output.json")
    parser.add_argument("--permission-note")
    args = parser.parse_args()
    try:
        if args.command == "serve":
            import uvicorn
            from .web import create_app

            print(f"Open http://127.0.0.1:{args.port}; Ctrl+C to stop", flush=True)
            uvicorn.run(create_app(args.db), host="127.0.0.1", port=args.port)
        elif args.command == "evaluate":
            if not args.input:
                parser.error("evaluate requires --input")
            result = evaluate(args.input)
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({key: value for key, value in result.items() if key not in ("results", "confusion_matrix")}, indent=2))
        elif args.command == "seed":
            print(json.dumps(Inbox(args.db).import_messages(Path(__file__).with_name("sample_messages.json").read_text(encoding="utf-8"),
                                                           ".json", "Author-generated synthetic demo")))
        elif args.command == "import":
            if not args.input or not args.permission_note:
                parser.error("import requires --input and --permission-note")
            path = Path(args.input)
            print(json.dumps(Inbox(args.db).import_messages(path.read_text(encoding="utf-8-sig"), path.suffix, args.permission_note)))
        else:
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(Inbox(args.db).export(), encoding="utf-8-sig")
            print(f"Approved drafts exported: {output}")
    except (ValueError, OSError) as error:
        parser.exit(2, f"Error: {error}\n")


if __name__ == "__main__":
    main()

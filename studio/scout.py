import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

SKILLS = {"python", "excel", "csv", "api", "automation", "sqlite"}
RISK_TERMS = {"captcha", "spam", "scraping", "unlimited", "guaranteed profit"}


def evaluate(jobs):
    result, seen = [], set()
    for job in jobs:
        if job.get("source_type") != "manual_export" or not job.get("permission_note", "").strip():
            raise ValueError("Only authorized manual exports with permission_note are enabled")
        if not all(isinstance(job.get(key), str) and job[key].strip() for key in ("id", "title", "description", "source")):
            raise ValueError("Job requires id, title, description and source")
        identity = (job["source"], job["id"])
        if identity in seen:
            continue
        seen.add(identity)
        url = job.get("url", "")
        if url and urlparse(url).scheme not in ("http", "https"):
            raise ValueError("Job URL must use HTTP(S)")
        text = (job["title"] + " " + job["description"]).lower()
        skills = sorted(skill for skill in SKILLS if skill in text)
        risks = sorted(term for term in RISK_TERMS if term in text)
        result.append({**job, "score": max(0, min(100, len(skills) * 20 - len(risks) * 30)),
                       "matched_skills": skills, "risk_flags": risks, "decision": "manual_review",
                       "questions": ["What are acceptance criteria?", "Can you provide anonymized sample data?"],
                       "outreach_sent": False})
    return sorted(result, key=lambda item: (-item["score"], item["id"]))


def main():
    parser = argparse.ArgumentParser(description="Offline scoring; no network or outreach")
    parser.add_argument("input")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        jobs = json.loads(Path(args.input).read_text(encoding="utf-8"))
        if not isinstance(jobs, list):
            raise ValueError("Expected JSON array")
        ranked = evaluate(jobs)
        Path(args.output).write_text(json.dumps(ranked, ensure_ascii=False, indent=2), encoding="utf-8")
    except (ValueError, TypeError, AttributeError, OSError) as error:
        parser.exit(2, f"Error: {error}\n")


if __name__ == "__main__":
    main()

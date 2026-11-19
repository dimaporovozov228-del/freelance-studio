import json
import re
import queue
import threading
from typing import Protocol

CATEGORIES = ("billing", "support", "sales", "unknown")
RULE_VERSION = "rules-v1"
ADAPTER_TIMEOUT = 3.0
ADAPTER_SLOTS = threading.BoundedSemaphore(2)


class ClassifierAdapter(Protocol):
    def classify(self, body: str, timeout: float) -> str: ...


PATTERNS = {
    "billing": r"\b(invoice\w*|payment\w*|refund\w*|billing|оплат\w*|сч[её]т\w*|возврат\w*)\b",
    "support": r"\b(error\w*|broken|crash\w*|not working|ошиб\w*|не работает|сломан\w*|сбой\w*)\b",
    "sales": r"\b(quote\w*|pricing|purchase\w*|buy|купить|стоимост\w*|коммерческ\w*|закуп\w*)\b",
}
TEMPLATES = {
    "billing": "Здравствуйте! Спасибо за обращение по оплате. Уточните, пожалуйста, номер заказа и суть вопроса. После проверки подготовим ответ.",
    "support": "Здравствуйте! Спасибо за сообщение о проблеме. Опишите шаги её воспроизведения и ожидаемый результат; при возможности приложите обезличенный скриншот.",
    "sales": "Здравствуйте! Спасибо за интерес. Уточните нужные товары или услуги, объём и желаемые сроки, чтобы мы подготовили предложение.",
    "unknown": "Здравствуйте! Спасибо за обращение. Уточните, пожалуйста, вопрос и ожидаемый результат.",
}


def redact(text):
    text = re.sub(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", "[EMAIL]", text, flags=re.I)
    return re.sub(r"(?<!\w)\+?\d[\d ()-]{6,}\d(?!\w)", "[PHONE_OR_NUMBER]", text)


def classify(body):
    matches = [category for category, pattern in PATTERNS.items() if re.search(pattern, body, re.I)]
    suspicious = bool(re.search(r"ignore.{0,50}instructions|игнорир\w*.{0,50}инструкц|auto.?approve|автоматически.{0,20}одобр", body, re.I | re.S))
    category = matches[0] if len(matches) == 1 and not suspicious else "unknown"
    return {"category": category, "confidence": 0.9 if category != "unknown" else 0.0,
            "priority": "high" if re.search(r"\b(urgent|asap|сроч\w*)\b", body, re.I) else "normal",
            "rationale": "Instruction-like content: manual review" if suspicious else "Matched categories: " + (", ".join(matches) or "none"),
            "engine": RULE_VERSION}


def adapter_call(adapter, body):
    if not ADAPTER_SLOTS.acquire(blocking=False):
        raise TimeoutError("Adapter worker limit reached")
    results = queue.Queue(maxsize=1)

    def run():
        try:
            results.put((True, adapter.classify(body, timeout=ADAPTER_TIMEOUT)))
        except Exception:
            results.put((False, None))
        finally:
            ADAPTER_SLOTS.release()

    threading.Thread(target=run, daemon=True).start()
    try:
        succeeded, response = results.get(timeout=ADAPTER_TIMEOUT)
    except queue.Empty as error:
        raise TimeoutError("Adapter timed out") from error
    if not succeeded:
        raise RuntimeError("Adapter failed")
    return response


def classify_with_adapter(body, adapter=None):
    redacted = redact(body)
    baseline = classify(redacted)
    if adapter is None or baseline["category"] == "unknown":
        return baseline
    for attempt in range(2):
        try:
            raw = adapter_call(adapter, redacted[:4000])
            if not isinstance(raw, str) or len(raw) > 4096:
                raise ValueError("Adapter response must be a short JSON string")
            result = json.loads(raw)
            if not isinstance(result, dict) or set(result) != {"category", "confidence", "priority", "rationale"}:
                raise ValueError("Invalid adapter schema")
            if result["category"] not in CATEGORIES or result["priority"] not in ("high", "normal"):
                raise ValueError("Invalid adapter values")
            if type(result["confidence"]) not in (int, float) or not 0 <= result["confidence"] <= 1:
                raise ValueError("Invalid confidence")
            if not isinstance(result["rationale"], str) or len(result["rationale"]) > 500:
                raise ValueError("Invalid rationale")
            result["rationale"] = redact(result["rationale"])
            return {**result, "engine": "adapter-v1"}
        except (ValueError, TypeError, TimeoutError, OSError, RuntimeError):
            continue
    return {**baseline, "rationale": baseline["rationale"] + "; adapter failed, rules fallback"}

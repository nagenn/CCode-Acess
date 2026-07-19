import json
import os
import pdfplumber
import openai
from openai import OpenAI
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
OPENAI_MODEL = "gpt-4o-mini"


def extract_contract_text(pdf_path: str) -> str:
    text = ""
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    return text


def load_compliance_rules(path: str = "compliance_rules.json") -> dict:
    with open(path, "r") as f:
        return json.load(f)


def build_prompt(contract_text: str, rules: dict) -> str:
    payment_threshold = rules.get("risk_thresholds", {}).get("payment_terms", "not specified")
    return f"""
You are a legal contract review assistant.

Your task is to review the contract strictly against the provided compliance rules.
Do NOT invent rules. Do NOT provide legal advice.

CONTRACT TEXT:
{contract_text}

COMPLIANCE RULES:
{json.dumps(rules, indent=2)}

Follow these steps:
1. Identify missing required clauses.
2. Identify prohibited or risky terms.
3. Check the contract's payment terms against risk_thresholds.payment_terms
   (currently: "{payment_threshold}"). A payment period at or within that
   threshold is compliant — set payment_terms_ok to true. A shorter payment
   period than the threshold is always compliant too, never a violation.
   Only a payment period that exceeds the threshold is non-compliant — set
   payment_terms_ok to false. If payment terms cannot be identified in the
   contract text, set payment_terms_ok to null.
4. Assess overall risk (Low, Medium, High).
5. Extract key obligations or deadlines.
6. Provide recommendations if risk is Medium or High.
7. Estimate your confidence (0.0-1.0).

Return ONLY valid JSON in this format:
{{
  "risk_score": "Low | Medium | High",
  "missing_clauses": [],
  "problematic_terms": [],
  "payment_terms_ok": true,
  "key_obligations": [],
  "recommendations": "",
  "confidence": 0.0
}}
"""


def run_analysis(pdf_path: str, rules_path: str = "compliance_rules.json") -> dict:
    """
    Main entry point. Takes a PDF path, returns analysis dict.
    Called by the FastAPI endpoint.
    """
    contract_text = extract_contract_text(pdf_path)
    if not contract_text.strip():
        return {
            "risk_score": "Unknown",
            "missing_clauses": [],
            "problematic_terms": [],
            "payment_terms_ok": None,
            "key_obligations": [],
            "recommendations": "Could not extract text from PDF.",
            "confidence": 0.0
        }

    rules = load_compliance_rules(rules_path)

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=0,
            messages=[{"role": "user", "content": build_prompt(contract_text, rules)}]
        )
    except openai.OpenAIError as e:
        return {
            "risk_score": "Unknown",
            "missing_clauses": [],
            "problematic_terms": [],
            "payment_terms_ok": None,
            "key_obligations": [],
            "recommendations": f"AI analysis failed: {e}.",
            "confidence": 0.0
        }

    raw = response.choices[0].message.content

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        clean = raw.replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(clean)
        except json.JSONDecodeError:
            return {
                "risk_score": "Unknown",
                "missing_clauses": [],
                "problematic_terms": [],
                "payment_terms_ok": None,
                "key_obligations": [],
                "recommendations": "AI response could not be parsed.",
                "confidence": 0.0
            }

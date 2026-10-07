"""WF004 Product Description Generator (LLM + guardrails against invented facts)."""
import json
import re

from agent.models import NeedsInput
from agent.registry import step, workflow_meta

FIELDS = ["name", "category", "attributes", "material", "color", "target_audience"]
MATERIAL_WORDS = {"cotton", "leather", "wool", "polyester", "silk", "linen", "steel", "plastic", "wood",
                  "bamboo", "nylon", "denim", "rubber", "glass", "ceramic", "metal", "aluminium", "aluminum"}

workflow_meta("WF004", keywords=["generate", "description", "seo", "content", "meta", "title", "copy", "product content"],
              param_keys=["product", "product_file"])


@step("WF004")
def validate_required_attributes(ctx):
    product = ctx.params.get("product")

    # The router may pass the natural-language request as "product".
    # Only accept it when it is actually a product dictionary.
    if isinstance(product, str):
        try:
            parsed = json.loads(product)
            product = parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError):
            product = None

    if not isinstance(product, dict):
        product = ctx.call_tool(
            "json_reader",
            path=ctx.param("product_file", "data/samples/product_input.json"),
        )
    product = {k: str(product.get(k, "") or "").strip() for k in FIELDS}
    if not product["name"] and not product["category"]:
        raise NeedsInput("I need at least a product name or category to write content. Please provide them.",
                         ["product"])
    missing = [k for k in FIELDS if not product[k]]
    ctx.state.update(product=product, missing=missing)
    ctx.decide("Missing attributes are never invented; they are listed explicitly in the output")
    return f"missing: {', '.join(missing)}" if missing else "all attributes present"


def _offline(product: dict, missing: list[str]) -> dict:
    """Template 'LLM' used when no API key is set. Uses ONLY supplied facts."""
    name = product["name"] or f"{product['category']} product"
    bits = []
    if product["color"]: bits.append(f"in {product['color'].lower()}")
    if product["material"]: bits.append(f"made from {product['material'].lower()}")
    lead = f"The {name}" + (" " + " ".join(bits) if bits else "") + "."
    feat = f" Key features: {product['attributes']}." if product["attributes"] else ""
    aud = f" Designed for {product['target_audience']}." if product["target_audience"] else ""
    desc = lead + feat + aud
    short = f"{name}" + (f" - {product['color']}" if product["color"] else "") + (f". {product['attributes'].split(',')[0].strip().capitalize()}." if product["attributes"] else ".")
    seo = f"{name}" + (f" | {product['category']}" if product["category"] else "")
    meta = f"Shop the {name}" + (f" in {product['color'].lower()}" if product["color"] else "") + \
           (f" - {product['attributes']}" if product["attributes"] else "") + "."
    return {"description": desc, "short_description": short, "seo_title": seo[:60], "meta_description": meta[:160]}


@step("WF004")
def create_product_description(ctx):
    p, missing = ctx.state["product"], ctx.state["missing"]
    system = ("You are an e-commerce copywriter. Use ONLY the facts provided. "
              "NEVER invent or assume missing attributes (material, sizes, certifications, claims). "
              "If an attribute is missing, simply do not mention it. "
              "Return JSON with keys: description, short_description, seo_title, meta_description.")
    user = (f"Product facts: {json.dumps({k: v for k, v in p.items() if v})}\n"
            f"Missing (do not mention or guess): {missing}\n"
            "Limits: seo_title <= 60 chars, meta_description <= 160 chars, short_description <= 25 words.")
    data, src = ctx.llm_json(system, user, offline=lambda: _offline(p, missing))
    ctx.state.update(content=data, source=src)
    return f"description generated ({src})"


@step("WF004")
def generate_short_description(ctx):
    c = ctx.state["content"]
    c["short_description"] = c.get("short_description", "").strip()
    n = len(c["short_description"].split())
    if n > 25:
        c["short_description"] = " ".join(c["short_description"].split()[:25]); ctx.warn("short description trimmed to 25 words")
    return f"{n} words"


@step("WF004")
def generate_seo_title(ctx):
    c = ctx.state["content"]
    if len(c.get("seo_title", "")) > 60:
        c["seo_title"] = c["seo_title"][:57].rstrip() + "..."; ctx.warn("SEO title trimmed to 60 chars")
    return f"{len(c['seo_title'])} chars"


@step("WF004")
def generate_meta_description(ctx):
    c, p = ctx.state["content"], ctx.state["product"]
    if len(c.get("meta_description", "")) > 160:
        c["meta_description"] = c["meta_description"][:157].rstrip() + "..."; ctx.warn("meta description trimmed to 160 chars")
    # guardrail: detect invented materials the user never supplied
    supplied = " ".join(p.values()).lower()
    text = " ".join(str(v) for v in c.values()).lower()
    invented = sorted(w for w in MATERIAL_WORDS if re.search(rf"\b{w}\b", text) and w not in supplied)
    if invented:
        ctx.warn(f"Guardrail: generated text mentions {invented}, which was not in the input - please review")
    missing = ctx.state["missing"]
    ctx.output = {**c, "missing_information": missing, "generated_by": ctx.state["source"]}
    ctx.report = (f"Product: {p['name']}\n\n  Description:\n    {c['description']}\n\n  Short description:\n    {c['short_description']}\n\n"
                  f"  SEO title ({len(c['seo_title'])}/60):\n    {c['seo_title']}\n\n"
                  f"  Meta description ({len(c['meta_description'])}/160):\n    {c['meta_description']}\n\n"
                  f"  MISSING INFORMATION (not invented): {', '.join(missing) if missing else 'none'}\n"
                  f"  Generated by: {ctx.state['source']}"
                  + (f"\n  Warnings: {'; '.join(ctx.warnings)}" if ctx.warnings else ""))
    return "length limits checked; missing info marked"

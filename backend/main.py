import os
import re
import json
import requests
from urllib.parse import urlparse
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from groq import Groq
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = FastAPI(title="IdeaLens API", version="1.0.0")

# Enable CORS for all origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MODEL = "llama-3.3-70b-versatile"


def get_groq_client() -> Groq:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY environment variable is not set.")
    return Groq(api_key=api_key)


class AnalyzeRequest(BaseModel):
    idea: str


def clean_and_parse_json(text: str) -> Dict[str, Any]:
    """
    Strips markdown code fences from LLM output and parses it into a dict.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        match = re.search(r"^(?:```(?:json)?\n)?(.*?)(?:\n```)?$", cleaned, re.DOTALL)
        if match:
            cleaned = match.group(1).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass
        raise ValueError(f"Failed to parse JSON response: {text[:200]}...") from e


def call_groq_json(system_prompt: str, user_content: str) -> Dict[str, Any]:
    """
    Calls the Groq API and returns a parsed JSON response.
    """
    client = get_groq_client()
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content
        return clean_and_parse_json(content)
    except Exception as e:
        # Retry without response_format, asking for JSON explicitly in the prompt
        print(f"Groq API call with response_format=json failed: {e}. Retrying without response_format.")
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": system_prompt
                        + "\nIMPORTANT: Return ONLY raw JSON. Do not wrap in markdown or code blocks.",
                    },
                    {"role": "user", "content": user_content},
                ],
                temperature=0.2,
            )
            content = response.choices[0].message.content
            return clean_and_parse_json(content)
        except Exception as retry_err:
            raise HTTPException(status_code=500, detail=f"Groq API call failed: {str(retry_err)}")


def extract_domain(url: str) -> str:
    """Extract the root domain from a URL for deduplication."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        # Remove www. prefix
        hostname = hostname.replace("www.", "")
        return hostname.lower()
    except Exception:
        return url.lower()


def search_competitors(query: str) -> List[Dict[str, Any]]:
    """
    Searches for competitors using the Tavily API.
    """
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        print("Warning: TAVILY_API_KEY is not set. Returning empty list.")
        return []

    try:
        from tavily import TavilyClient
        tavily_client = TavilyClient(api_key=api_key)
        response = tavily_client.search(query=query, max_results=5)
        return response.get("results", [])
    except Exception as e:
        print(f"Tavily SDK error: {e}. Falling back to REST request.")
        try:
            res = requests.post(
                "https://api.tavily.com/search",
                json={"api_key": api_key, "query": query, "max_results": 5, "search_depth": "basic"},
                timeout=12,
            )
            res.raise_for_status()
            return res.json().get("results", [])
        except Exception as req_err:
            print(f"Tavily REST fallback also failed: {req_err}")
            return []


def fetch_jina_content(url: str, max_chars: int = 3000) -> Optional[str]:
    """
    Fetches page content via Jina Reader. Returns None on failure or if content is too short.
    """
    try:
        jina_url = f"https://r.jina.ai/{url}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        }
        res = requests.get(jina_url, headers=headers, timeout=15)
        res.raise_for_status()
        content = res.text
        if len(content) < 200:
            print(f"Jina Reader returned < 200 chars for {url}, marking as dead.")
            return None
        return content[:max_chars]
    except Exception as e:
        print(f"Jina Reader failed for {url}: {e}")
        return None


def classify_page(content: str) -> Dict[str, Any]:
    """
    CHANGE 3: Source Classifier — classifies a page as product_homepage,
    listicle_or_review_site, or irrelevant.
    """
    system_prompt = (
        "Classify this webpage content. Return ONLY valid JSON with keys:\n"
        "- page_type: one of 'product_homepage', 'listicle_or_review_site', 'irrelevant'\n"
        "- product_name: if page_type is product_homepage, the actual product/company name "
        "as it appears on the page. Otherwise null.\n"
        "- mentioned_products: if page_type is listicle_or_review_site, array of real product "
        "names mentioned in the content (max 8). Otherwise empty array.\n\n"
        "Rules: A page is 'product_homepage' ONLY if it is clearly the official site/landing "
        "page of ONE specific product, written in first-person marketing voice about that product. "
        "A page listing, comparing, or reviewing MULTIPLE products (review aggregators, 'top 10' "
        "articles, G2/Capterra-style category pages) is 'listicle_or_review_site', even if it has "
        "pricing or feature tables. When in doubt between product_homepage and "
        "listicle_or_review_site, choose listicle_or_review_site."
    )
    # Use first 2000 chars for classification (cheap and fast)
    truncated = content[:2000]
    try:
        return call_groq_json(system_prompt, truncated)
    except Exception as e:
        print(f"Classification call failed: {e}")
        return {"page_type": "irrelevant", "product_name": None, "mentioned_products": []}


def extract_competitor_profile(url: str, content: str) -> Optional[Dict[str, Any]]:
    """
    CHANGE 4: Grounded Competitor Intelligence extraction with evidence snippets.
    """
    system_prompt = (
        "You are a competitive intelligence analyst. You will be given raw text scraped from "
        "a product's real website. Extract ONLY information that is explicitly present in the text.\n\n"
        "Return ONLY valid JSON with keys: name (string), tagline (string), core_features (array, max 6), "
        "target_market (string), pricing_model (string), strengths (array, max 3), "
        "weaknesses (array, max 3), evidence (object).\n\n"
        "CRITICAL RULES:\n"
        "- If pricing is not mentioned anywhere in the text, set pricing_model to "
        "'Not stated on website' — do NOT guess or use generic phrases like 'subscription-based' "
        "unless those exact words or clear pricing numbers appear in the text.\n"
        "- Weaknesses must be inferred ONLY from what's absent or limited based on the actual "
        "feature list you extracted — not generic SaaS weaknesses. If you cannot reasonably infer "
        "a weakness from the text, return fewer than 3 items rather than inventing one.\n"
        "- core_features must be features explicitly described in the text. Do not add common "
        "features of 'similar tools in general.'\n"
        "- Every value you output must be traceable to a specific phrase in the provided text. "
        "If a field truly cannot be filled from the text, use 'Not stated on website' (for strings) "
        "or an empty array (for lists) rather than inventing content.\n\n"
        "The 'evidence' field must be an object mapping each of the 7 main keys "
        "(name, tagline, core_features, target_market, pricing_model, strengths, weaknesses) "
        "to the exact short snippet (max 15 words) from the source text that justifies that value."
    )

    try:
        profile = call_groq_json(system_prompt, f"Product URL: {url}\n\nContent:\n{content}")
        profile["url"] = url
        return profile
    except Exception as e:
        print(f"Groq analysis failed for competitor {url}: {e}")
        return None


def check_relevance(idea_text: str, parsed_idea: Dict[str, Any], content: str) -> Dict[str, Any]:
    """
    Tightened relevance gate: requires job-level overlap, not category-level overlap.
    First classifies entity_type (software vs non-software), then checks whether
    the candidate shares >=2 of the user's key_features as primary functions.
    Returns {entity_type: str, is_relevant: bool, relevance_reason: str}.
    """
    system_prompt = (
        "You are screening competitor candidates for relevance. You will be "
        "given: (1) the user's startup idea description and its key_features, "
        "and (2) raw text scraped from a candidate's website.\n\n"
        "Return ONLY valid JSON with keys:\n"
        "- entity_type: one of 'software_product', 'course_or_certification', "
        "'consulting_or_agency', 'hardware_or_physical_good', 'media_or_entertainment', "
        "'other_non_product'\n"
        "- is_relevant: boolean\n"
        "- relevance_reason: one sentence\n\n"
        "Step 1 — Determine entity_type from the actual page content. If "
        "entity_type is anything other than 'software_product', set "
        "is_relevant to false automatically, regardless of keyword overlap. "
        "Training providers, courses, certifications, agencies, and "
        "consultancies are NEVER relevant competitors to a software idea, "
        "even if their marketing copy uses words like 'AI,' 'platform,' or "
        "'revolutionize.'\n\n"
        "Step 2 — If entity_type is 'software_product', evaluate JOB-LEVEL "
        "overlap, not category-level overlap. Ask: 'If a user adopted this "
        "candidate product instead of the user's idea, would it actually "
        "solve the same core job?'\n\n"
        "Use this test: compare the candidate's core function against the "
        "user's key_features list. is_relevant is true ONLY if the candidate "
        "shares at least 2 of the user's specific key_features as a primary "
        "function (not a minor/secondary feature buried in a larger suite).\n\n"
        "Examples of what must be marked false even though they sound related:\n"
        "- A general note-taking app, when the idea is specifically about "
        "AI-driven task prioritization — note-taking is not the same job.\n"
        "- A time-tracking tool, when the idea is about task prioritization "
        "and habit learning — time tracking is a different job.\n"
        "- A video conferencing/chat platform, when the idea is a task "
        "management app — communication is not task management, even if "
        "the platform has a task list as a minor feature.\n"
        "- A broad all-in-one workspace (notes + docs + wiki + tasks), unless "
        "its task-management capability specifically matches 2+ of the "
        "user's key_features (e.g. AI prioritization, habit learning) — "
        "having 'tasks' as one of ten features in a different-purpose tool "
        "is not enough.\n\n"
        "Be strict. When uncertain, set is_relevant to false. It is better to "
        "under-include than to show a borderline or wrong-job product as a "
        "direct competitor."
    )

    key_features = parsed_idea.get('key_features', [])
    idea_context = (
        f"USER'S STARTUP IDEA:\n{idea_text}\n\n"
        f"PARSED IDEA FIELDS:\n"
        f"Problem: {parsed_idea.get('problem', '')}\n"
        f"Solution: {parsed_idea.get('solution', '')}\n"
        f"Domain: {parsed_idea.get('domain', '')}\n"
        f"Target Users: {parsed_idea.get('target_users', '')}\n"
        f"Key Features (the candidate MUST match at least 2 of these as primary functions):\n"
        + "\n".join(f"  - {f}" for f in key_features)
        + f"\n\nCANDIDATE PRODUCT PAGE CONTENT (first 1500 chars):\n{content[:1500]}"
    )

    try:
        result = call_groq_json(system_prompt, idea_context)
        entity_type = result.get("entity_type", "other_non_product")
        is_relevant = bool(result.get("is_relevant", False))
        # Enforce: non-software entities are never relevant
        if entity_type != "software_product":
            is_relevant = False
        return {
            "entity_type": entity_type,
            "is_relevant": is_relevant,
            "relevance_reason": result.get("relevance_reason", "")
        }
    except Exception as e:
        print(f"Relevance check failed: {e}")
        # Default to not relevant on failure (strict)
        return {"entity_type": "other_non_product", "is_relevant": False, "relevance_reason": "Relevance check failed"}



@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/api/analyze")
def analyze_idea(request: AnalyzeRequest):
    if not request.idea.strip():
        raise HTTPException(status_code=400, detail="Idea text cannot be empty.")

    print(f"Starting analysis for idea: {request.idea[:60]}...")

    # =========================================================================
    # AGENT 1: Idea Parser (CHANGE 1 — upgraded prompt)
    # =========================================================================
    print("Running Agent 1: Idea Parser...")
    agent1_prompt = (
        "You are a startup analyst with broad knowledge of existing software products. "
        "Read the user's idea, however casually phrased. Extract:\n"
        "- problem (string)\n"
        "- solution (string)\n"
        "- domain (string)\n"
        "- target_users (string)\n"
        "- key_features (array of 5 strings)\n"
        "- unique_angle (string)\n"
        "- likely_known_competitors: array of up to 5 REAL product names you already know of "
        "that solve a similar problem (empty array if genuinely none come to mind — do not invent names)\n"
        "- search_queries: array of 4 distinct search query strings designed to find REAL competing "
        "products. Include at least one query using a known competitor name (e.g. 'alternatives to "
        "Grammarly for business email tone'), one category-based query, and one feature-specific query. "
        "Each search query must include at least one concrete term from the user's specific idea "
        "(a feature, a target platform, or the exact problem) — not just generic category words like "
        "'AI tool' or 'productivity app' alone. A query must be specific enough that an unrelated "
        "product in a different category would not plausibly rank for it.\n\n"
        "Return ONLY valid JSON, no markdown fences."
    )
    parsed_idea = call_groq_json(agent1_prompt, request.idea)
    print("Parsed Idea:", json.dumps(parsed_idea, indent=2)[:500])

    # =========================================================================
    # AGENT 2: Search & Discovery (CHANGE 2 — loop over all 4 search_queries)
    # =========================================================================
    print("Running Agent 2: Search & Discovery...")
    search_queries = parsed_idea.get("search_queries", [])
    if not search_queries:
        # Fallback: generate a basic query if Agent 1 didn't produce search_queries
        fallback_query = f"{parsed_idea.get('solution', '')} {parsed_idea.get('domain', '')} competitors alternatives"
        search_queries = [fallback_query]

    all_results = []
    seen_domains = set()

    for sq in search_queries:
        print(f"  Tavily search: {sq}")
        results = search_competitors(sq)
        for r in results:
            url = r.get("url", "")
            domain = extract_domain(url)
            if domain and domain not in seen_domains:
                seen_domains.add(domain)
                all_results.append(r)

    print(f"Agent 2: {len(all_results)} unique candidate URLs after deduplication across {len(search_queries)} queries.")

    # =========================================================================
    # NEW AGENT: Source Classifier (CHANGE 3 — insert before old Agent 3)
    # =========================================================================
    print("Running Source Classifier...")

    # Phase 1: Fetch and classify all candidates
    product_homepages = []        # list of {url, content, product_name}
    listicle_mentioned = set()    # product names mentioned in listicles
    covered_product_names = set() # product names already covered by homepage results

    for result in all_results:
        url = result.get("url", "")
        if not url:
            continue

        print(f"  Fetching & classifying: {url}")
        content = fetch_jina_content(url)
        if content is None:
            print(f"    → dead (fetch failed or < 200 chars)")
            continue

        classification = classify_page(content)
        page_type = classification.get("page_type", "irrelevant")

        if page_type == "product_homepage":
            pname = classification.get("product_name") or result.get("title", "Unknown")
            print(f"    → product_homepage: {pname}")
            # Relevance gate: check if this product actually competes with the idea
            rel = check_relevance(request.idea, parsed_idea, content)
            if not rel["is_relevant"]:
                print(f"    → DROPPED [{rel.get('entity_type', 'unknown')}]: {rel['relevance_reason']}")
                continue
            print(f"    → RELEVANT [{rel.get('entity_type', 'software_product')}]: {rel['relevance_reason']}")
            product_homepages.append({
                "url": url,
                "content": content,
                "product_name": pname,
            })
            covered_product_names.add(pname.lower().strip())
        elif page_type == "listicle_or_review_site":
            mentioned = classification.get("mentioned_products", [])
            print(f"    → listicle, mentioned products: {mentioned}")
            for name in mentioned:
                if name and name.lower().strip() not in covered_product_names:
                    listicle_mentioned.add(name.strip())
        else:
            print(f"    → irrelevant, dropping")

    # Phase 2: Follow up on listicle-mentioned products not already covered
    if listicle_mentioned:
        print(f"Source Classifier Phase 2: Following up on {len(listicle_mentioned)} listicle-mentioned products...")
        for product_name in listicle_mentioned:
            if product_name.lower().strip() in covered_product_names:
                continue
            print(f"  Searching for official site: {product_name}")
            followup_results = search_competitors(f"{product_name} official website")
            if followup_results:
                top_result = followup_results[0]
                fu_url = top_result.get("url", "")
                fu_domain = extract_domain(fu_url)
                # Skip if we already have this domain
                if fu_domain in seen_domains:
                    print(f"    → already have domain {fu_domain}, skipping")
                    continue
                seen_domains.add(fu_domain)

                fu_content = fetch_jina_content(fu_url)
                if fu_content is None:
                    print(f"    → dead")
                    continue
                fu_class = classify_page(fu_content)
                if fu_class.get("page_type") == "product_homepage":
                    pname = fu_class.get("product_name") or product_name
                    print(f"    → confirmed product_homepage: {pname}")
                    # Relevance gate for listicle-mined candidates too
                    rel = check_relevance(request.idea, parsed_idea, fu_content)
                    if not rel["is_relevant"]:
                        print(f"    → DROPPED [{rel.get('entity_type', 'unknown')}]: {rel['relevance_reason']}")
                        continue
                    print(f"    → RELEVANT [{rel.get('entity_type', 'software_product')}]: {rel['relevance_reason']}")
                    product_homepages.append({
                        "url": fu_url,
                        "content": fu_content,
                        "product_name": pname,
                    })
                    covered_product_names.add(pname.lower().strip())
                else:
                    print(f"    → not a product homepage ({fu_class.get('page_type')}), dropping")

    print(f"Source Classifier complete: {len(product_homepages)} relevant software competitors (after strict job-level filtering).")

    # =========================================================================
    # AGENT 3: Competitor Intelligence (CHANGE 4 + CHANGE 5 — grounded, all results)
    # =========================================================================
    print("Running Agent 3: Competitor Intelligence (grounded extraction)...")
    competitors = []
    for homepage in product_homepages:  # CHANGE 5: no slice — process ALL
        url = homepage["url"]
        content = homepage["content"]
        print(f"  Extracting profile: {url}")
        profile = extract_competitor_profile(url, content)
        if profile:
            competitors.append(profile)

    print(f"Agent 3 complete: {len(competitors)} competitor profiles extracted.")

    # =========================================================================
    # AGENT 4: Strategy Agent (CHANGE 7 — core_features grounding)
    # =========================================================================
    print("Running Agent 4: Strategy Agent...")
    agent4_prompt = (
        "You are a startup strategist. Compare this idea against these competitors. "
        "Return ONLY valid JSON with keys:\n"
        "- feature_matrix: object where each key is a feature name (8 features total), "
        "value is object with keys: user_idea (boolean), and one key per competitor name (boolean)\n"
        "- idea_scores: object with keys innovation, market_gap, feasibility, competition_density — each a number 0-100\n"
        "- competitor_scores: array of objects, each with name and scores object (same 4 keys, each 0-100)\n"
        "- suggestions: array of 5 objects each with keys: title (string), description (string), priority (High/Medium/Low)\n"
        "- summary: 2 sentence executive summary string\n\n"
        "Base feature_matrix and comparison entirely on the core_features arrays provided for each "
        "competitor — do not assume a competitor has a feature unless it appears in their core_features list."
    )

    strategy_payload = {"parsed_idea": parsed_idea, "competitors": competitors}
    strategy = call_groq_json(agent4_prompt, json.dumps(strategy_payload))
    print("Strategy summary:", strategy.get("summary"))

    return {
        "parsed_idea": parsed_idea,
        "competitors": competitors,
        "strategy": strategy,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

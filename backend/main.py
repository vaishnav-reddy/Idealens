import os
import re
import json
import asyncio
import time as _time_module
import requests
import httpx
from collections import Counter
from urllib.parse import urlparse
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from groq import Groq
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# =====================================================================
# Jina API key support (FIX 1)
# =====================================================================
_JINA_API_KEY = os.getenv("JINA_API_KEY", "").strip()
if not _JINA_API_KEY:
    print(
        "WARNING: JINA_API_KEY not set — Jina requests will share a strict "
        "IP-wide rate limit. Add a free key from jina.ai for reliable operation."
    )

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
FALLBACK_MODEL = "llama-3.1-8b-instant"
MAX_CANDIDATES = 5  # Tier 2 (general search URLs) cap — reduced from 7


# =====================================================================
# CHANGE 2: Domain blocklist — skip Jina fetch + Groq entirely
# =====================================================================
BLOCKED_DOMAINS: frozenset = frozenset([
    "wikipedia.org", "youtube.com", "linkedin.com", "play.google.com",
    "apps.apple.com", "reddit.com", "twitter.com", "x.com", "medium.com",
    "quora.com", "trustpilot.com", "facebook.com", "instagram.com",
    "glassdoor.com", "yelp.com", "g2.com", "capterra.com", "producthunt.com",
])


def is_blocked_domain(url: str) -> bool:
    """
    Returns True if the URL's root domain is in the BLOCKED_DOMAINS set.
    Logs the skip for diagnostics.
    """
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower().removeprefix("www.")
        # Check exact match and suffix match (e.g. sub.wikipedia.org)
        for blocked in BLOCKED_DOMAINS:
            if hostname == blocked or hostname.endswith("." + blocked):
                return True
        return False
    except Exception:
        return False


# =====================================================================
# FIX 1: Concurrency / rate-limit throttle
# =====================================================================
class GroqThrottle:
    """
    Wraps asyncio.Semaphore(max_concurrent) + enforces a minimum stagger
    between request starts, preventing TPM bursts.
    """

    def __init__(self, max_concurrent: int = 3, min_stagger_secs: float = 1.5):
        self._sem = asyncio.Semaphore(max_concurrent)
        self._lock = asyncio.Lock()
        self._last_request_time: float = 0.0
        self._min_stagger = min_stagger_secs

    async def acquire(self):
        await self._sem.acquire()
        async with self._lock:
            now = _time_module.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self._min_stagger:
                await asyncio.sleep(self._min_stagger - elapsed)
            self._last_request_time = _time_module.monotonic()

    def release(self):
        self._sem.release()

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, *args):
        self.release()


_groq_throttle = GroqThrottle(max_concurrent=3, min_stagger_secs=1.5)


# =====================================================================
# FIX 2: Dedicated Jina concurrency limiter (separate from Groq)
# =====================================================================
class JinaThrottle:
    """
    Limits concurrent Jina Reader requests to 2 and staggers starts by
    at least 500 ms to stay under the 100 RPM free-tier ceiling.
    """

    def __init__(self, max_concurrent: int = 2, min_stagger_secs: float = 0.5):
        self._sem = asyncio.Semaphore(max_concurrent)
        self._lock = asyncio.Lock()
        self._last_request_time: float = 0.0
        self._min_stagger = min_stagger_secs

    async def acquire(self):
        await self._sem.acquire()
        async with self._lock:
            now = _time_module.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self._min_stagger:
                await asyncio.sleep(self._min_stagger - elapsed)
            self._last_request_time = _time_module.monotonic()

    def release(self):
        self._sem.release()

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, *args):
        self.release()


_jina_throttle = JinaThrottle(
    max_concurrent=2 if _JINA_API_KEY else 1,
    min_stagger_secs=0.5 if _JINA_API_KEY else 1.0,
)


def get_groq_client() -> Groq:
    load_dotenv(override=True)
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


# =====================================================================
# FIX 4: Helpers for retry-after parsing and error classification
# =====================================================================

def _parse_retry_after(error_str: str) -> float:
    """Parse the 'try again in Xs' or 'try again in XmYs' from a Groq error message."""
    m = re.search(r"try again in (\d+(?:\.\d+)?)s", error_str, re.IGNORECASE)
    if m:
        return float(m.group(1)) + 1.0
    m = re.search(r"try again in (\d+)m(\d+(?:\.\d+)?)s", error_str, re.IGNORECASE)
    if m:
        return int(m.group(1)) * 60 + float(m.group(2)) + 1.0
    m = re.search(r"try again in (\d+)m", error_str, re.IGNORECASE)
    if m:
        return int(m.group(1)) * 60 + 2.0
    return 10.0  # default fallback


def _is_tpd_error(error_str: str) -> bool:
    """Check if this is a daily (TPD) token limit error."""
    low = error_str.lower()
    return "tokens per day" in low or "tpd" in low or "daily" in low


def _is_rate_limit_error(error_str: str) -> bool:
    """Check if this is any kind of rate-limit error."""
    return "429" in error_str or "rate_limit" in error_str.lower()


# =====================================================================
# FIX 4: Rewritten call_groq_json with proper fallback chain
# =====================================================================

def call_groq_json(system_prompt: str, user_content: str, _retries: int = 3) -> Dict[str, Any]:
    """
    Calls the Groq API and returns a parsed JSON response.
    Fallback chain: llama-3.3-70b-versatile -> llama-3.1-8b-instant.
    On TPD error: switches model immediately. On TPM error: parses the
    server-provided retry-after delay and waits exactly that long + 1s buffer.
    """
    client = get_groq_client()
    current_model = MODEL

    for attempt in range(1, _retries + 1):
        try:
            response = client.chat.completions.create(
                model=current_model,
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
            error_str = str(e)

            if _is_rate_limit_error(error_str):
                # TPD on primary model -> switch to fallback for remaining retries
                if current_model == MODEL and _is_tpd_error(error_str):
                    print(f"  [RateLimit] TPD limit on {current_model}. Switching to {FALLBACK_MODEL}.")
                    current_model = FALLBACK_MODEL
                    continue  # retry immediately with the new model

                # TPM or other rate limit -> parse wait time and sleep
                wait_secs = min(_parse_retry_after(error_str), 60.0)

                if attempt < _retries:
                    print(f"  [RateLimit] {current_model} attempt {attempt}/{_retries}. Waiting {wait_secs:.1f}s...")
                    _time_module.sleep(wait_secs)
                    continue
                else:
                    raise HTTPException(
                        status_code=429,
                        detail=f"Groq rate limit exceeded after {_retries} retries on {current_model}. {error_str}"
                    )

            # Non-429 error: retry without response_format
            print(f"Groq API call with response_format=json failed: {e}. Retrying without response_format.")
            try:
                response = client.chat.completions.create(
                    model=current_model,
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
                error_retry_str = str(retry_err)
                if _is_rate_limit_error(error_retry_str) and attempt < _retries:
                    wait_secs = min(_parse_retry_after(error_retry_str), 60.0)
                    print(f"  [RateLimit] Retry fallback attempt {attempt}/{_retries}. Waiting {wait_secs:.1f}s...")
                    _time_module.sleep(wait_secs)
                    continue
                raise HTTPException(status_code=500, detail=f"Groq API call failed: {str(retry_err)}")


async def call_groq_json_async(system_prompt: str, user_content: str, _retries: int = 3) -> Dict[str, Any]:
    """
    Throttled async wrapper around call_groq_json.
    Acquires the GroqThrottle (semaphore + stagger) before making the API call.
    """
    async with _groq_throttle:
        return await asyncio.to_thread(call_groq_json, system_prompt, user_content, _retries)


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
    Searches for competitors using the Tavily API (sync version).
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


async def search_competitors_async(query: str) -> List[Dict[str, Any]]:
    """
    Async version of search_competitors. Tries httpx REST first, falls back to sync SDK via to_thread.
    """
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        print("Warning: TAVILY_API_KEY is not set. Returning empty list.")
        return []

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            res = await client.post(
                "https://api.tavily.com/search",
                json={"api_key": api_key, "query": query, "max_results": 5, "search_depth": "basic"},
            )
            res.raise_for_status()
            return res.json().get("results", [])
    except Exception as e:
        print(f"Async Tavily REST failed for '{query[:50]}': {e}. Falling back to sync SDK.")
        try:
            return await asyncio.to_thread(search_competitors, query)
        except Exception as fallback_err:
            print(f"Sync fallback also failed: {fallback_err}")
            return []


def _jina_auth_headers() -> Dict[str, str]:
    """Build Jina request headers, including Authorization if JINA_API_KEY is set."""
    headers: Dict[str, str] = {}
    if _JINA_API_KEY:
        headers["Authorization"] = f"Bearer {_JINA_API_KEY}"
    return headers


def fetch_jina_content(url: str, max_chars: int = 6000) -> Optional[str]:
    """
    Fetches page content via Jina Reader (sync). Returns None on failure or if content is too short.
    CHANGE 2: Checks domain blocklist before making the HTTP request.
    NOTE: The sync version has no concurrency control — use fetch_jina_content_async in the pipeline.
    """
    if is_blocked_domain(url):
        print(f"  [Blocklist] Skipping blocked domain (sync): {url} — skipped_blocked_domain")
        return None
    try:
        jina_url = f"https://r.jina.ai/{url}"
        res = requests.get(jina_url, headers=_jina_auth_headers(), timeout=15)
        res.raise_for_status()
        content = res.text
        if len(content) < 200:
            print(f"Jina Reader returned < 200 chars for {url}, marking as dead.")
            return None
        return content[:max_chars]
    except Exception as e:
        print(f"Jina Reader failed for {url}: {e}")
        return None


async def fetch_jina_content_async(url: str, max_chars: int = 6000) -> Optional[str]:
    """
    Async Jina Reader fetch. Acquires the dedicated _jina_throttle
    (Semaphore + stagger) before making the HTTP request.
    CHANGE 2: Checks domain blocklist before making the HTTP request — no Jina cost incurred.
    Retries up to 3 times on 403 with exponential backoff, since 403
    from Jina Reader typically means a transient IP-level rate limit.
    """
    # CHANGE 2: Pre-flight blocklist check — free, no Jina fetch needed
    if is_blocked_domain(url):
        print(f"  [Blocklist] Skipping blocked domain: {url} — skipped_blocked_domain")
        return None

    max_retries = 3
    backoff_secs = [5.0, 10.0, 20.0]

    async with _jina_throttle:
        for attempt in range(max_retries):
            try:
                jina_url = f"https://r.jina.ai/{url}"
                async with httpx.AsyncClient(timeout=20.0) as client:
                    res = await client.get(jina_url, headers=_jina_auth_headers())
                    if res.status_code == 403 and attempt < max_retries - 1:
                        wait = backoff_secs[attempt]
                        print(f"Jina 403 for {url} (attempt {attempt + 1}/{max_retries}), retrying in {wait}s...")
                        await asyncio.sleep(wait)
                        continue
                    res.raise_for_status()
                    content = res.text
                    if len(content) < 200:
                        print(f"Jina Reader returned < 200 chars for {url}, marking as dead.")
                        return None
                    return content[:max_chars]
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 403 and attempt < max_retries - 1:
                    wait = backoff_secs[attempt]
                    print(f"Jina 403 for {url} (attempt {attempt + 1}/{max_retries}), retrying in {wait}s...")
                    await asyncio.sleep(wait)
                    continue
                print(f"Jina Reader async failed for {url}: {e}")
                return None
            except Exception as e:
                print(f"Jina Reader async failed for {url}: {e}")
                return None
    return None


# =====================================================================
# Source Classifier (kept for standalone use; FIX 2: re-raise API errors)
# =====================================================================

def classify_page(content: str) -> Dict[str, Any]:
    """
    Source Classifier -- classifies a page as product_homepage,
    listicle_or_review_site, or irrelevant.
    FIX 2: Re-raises API errors (HTTPException, rate-limit) instead of
    swallowing them as 'irrelevant'.
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
    # Use first 5000 chars for classification (cheap and fast)
    truncated = content[:5000]
    try:
        return call_groq_json(system_prompt, truncated)
    except HTTPException:
        raise  # FIX 2: propagate API errors for retry queue
    except Exception as e:
        if _is_rate_limit_error(str(e)):
            raise  # FIX 2: propagate rate-limit errors
        print(f"Classification call failed: {e}")
        return {"page_type": "irrelevant", "product_name": None, "mentioned_products": []}


# =====================================================================
# FIX 3.2: Merged classify + relevance in ONE LLM call
# =====================================================================

def classify_and_check_relevance(idea_text: str, parsed_idea: Dict[str, Any], content: str) -> Dict[str, Any]:
    """
    Combined source classification + relevance check in a single LLM call.
    Returns {page_type, product_name, mentioned_products, entity_type, is_relevant, relevance_reason}.
    FIX 2: Re-raises API errors instead of swallowing them.
    """
    key_features = parsed_idea.get('key_features', [])

    system_prompt = (
        "You will classify a webpage AND evaluate its competitive relevance to a startup idea in one step.\n\n"
        "Return ONLY valid JSON with ALL of these keys:\n"
        "- page_type: one of 'product_homepage', 'listicle_or_review_site', 'irrelevant'\n"
        "- product_name: if page_type is 'product_homepage', the actual product/company name "
        "as it appears on the page. Otherwise null.\n"
        "- mentioned_products: if page_type is 'listicle_or_review_site', array of real product "
        "names mentioned in the content (max 8). Otherwise empty array.\n"
        "- entity_type: one of 'software_product', 'course_or_certification', "
        "'consulting_or_agency', 'hardware_or_physical_good', 'media_or_entertainment', "
        "'other_non_product'\n"
        "- is_relevant: boolean\n"
        "- relevance_reason: one sentence\n\n"

        "=== PAGE CLASSIFICATION RULES ===\n"
        "A page is 'product_homepage' ONLY if it is clearly the official site/landing "
        "page of ONE specific product, written in first-person marketing voice about that product. "
        "A page listing, comparing, or reviewing MULTIPLE products (review aggregators, 'top 10' "
        "articles, G2/Capterra-style category pages) is 'listicle_or_review_site', even if it has "
        "pricing or feature tables. When in doubt between product_homepage and "
        "listicle_or_review_site, choose listicle_or_review_site.\n\n"

        "If page_type is NOT 'product_homepage', set entity_type to 'other_non_product', "
        "is_relevant to false, and relevance_reason to 'Not a product homepage'.\n\n"

        "=== RELEVANCE RULES (apply ONLY when page_type is 'product_homepage') ===\n"
        "Step 1 -- Determine entity_type from the actual page content. If "
        "entity_type is anything other than 'software_product', set "
        "is_relevant to false automatically, regardless of keyword overlap. "
        "Training providers, courses, certifications, agencies, and "
        "consultancies are NEVER relevant competitors to a software idea, "
        "even if their marketing copy uses words like 'AI,' 'platform,' or "
        "'revolutionize.'\n\n"
        "Step 2 -- If entity_type is 'software_product', evaluate JOB-LEVEL "
        "overlap, not category-level overlap. Ask: 'If a user adopted this "
        "candidate product instead of the user's idea, would it actually "
        "solve the same core job?'\n\n"
        "Use this test: compare the candidate's core function against the "
        "user's key_features list. is_relevant is true ONLY if the candidate "
        "shares at least 2 of the user's specific key_features as a primary "
        "function (not a minor/secondary feature buried in a larger suite).\n\n"
        "Clarification for Large Broad Platforms:\n"
        "If the candidate is a large, multi-feature platform (e.g. an all-in-one project management or productivity suite), "
        "check whether the SPECIFIC feature matching the user's key_features is explicitly marketed as a current, named feature "
        "on the page (not assumed) -- even if it is one of many features listed, not the platform's sole focus. "
        "Do not penalize relevance for a tool that does many things, as long as at least one explicitly described feature "
        "directly matches at least one of the user's key_features (lower the previous '2 features' threshold to '1 explicit, named feature' "
        "when the candidate is a known broad-platform type, since broad platforms naturally dilute any single feature's "
        "prominence in their own marketing copy).\n\n"
        "Examples of what must be marked false even though they sound related:\n"
        "- A general note-taking app, when the idea is specifically about "
        "AI-driven task prioritization -- note-taking is not the same job.\n"
        "- A time-tracking tool, when the idea is about task prioritization "
        "and habit learning -- time tracking is a different job.\n"
        "- A video conferencing/chat platform, when the idea is a task "
        "management app -- communication is not task management, even if "
        "the platform has a task list as a minor feature.\n"
        "- A broad all-in-one workspace (notes + docs + wiki + tasks), unless "
        "its task-management capability specifically matches the user's "
        "key_features (applying the broad platform rules above).\n\n"
        "Be strict. When uncertain, set is_relevant to false. It is better to "
        "under-include than to show a borderline or wrong-job product as a "
        "direct competitor."
    )

    idea_context = (
        f"USER'S STARTUP IDEA:\n{idea_text}\n\n"
        f"PARSED IDEA FIELDS:\n"
        f"Problem: {parsed_idea.get('problem', '')}\n"
        f"Solution: {parsed_idea.get('solution', '')}\n"
        f"Domain: {parsed_idea.get('domain', '')}\n"
        f"Target Users: {parsed_idea.get('target_users', '')}\n"
        f"Key Features (candidate MUST match at least 2 as primary functions, or 1 explicit named feature if large platform):\n"
        + "\n".join(f"  - {f}" for f in key_features)
        + f"\n\nWEBPAGE CONTENT TO CLASSIFY AND EVALUATE (first 5000 chars):\n{content[:5000]}"
    )

    try:
        result = call_groq_json(system_prompt, idea_context)
        # Enforce: non-software entities are never relevant
        page_type = result.get("page_type", "irrelevant")
        if page_type == "product_homepage":
            entity_type = result.get("entity_type", "other_non_product")
            if entity_type != "software_product":
                result["is_relevant"] = False
        else:
            result["is_relevant"] = False
            result["entity_type"] = result.get("entity_type", "other_non_product")
        return result
    except HTTPException:
        raise  # FIX 2: propagate API errors for retry queue
    except Exception as e:
        if _is_rate_limit_error(str(e)):
            raise  # FIX 2: propagate rate-limit errors
        print(f"Classify+relevance call failed: {e}")
        return {
            "page_type": "irrelevant",
            "product_name": None,
            "mentioned_products": [],
            "entity_type": "other_non_product",
            "is_relevant": False,
            "relevance_reason": f"Classification failed: {e}",
        }


def extract_competitor_profile(url: str, content: str) -> Optional[Dict[str, Any]]:
    """
    Grounded Competitor Intelligence extraction with evidence snippets.
    Used for Tier 2 (general search) candidates that have already passed the classify+relevance gate.
    """
    system_prompt = (
        "You are a competitive intelligence analyst. You will be given raw text scraped from "
        "a product's real website. Extract ONLY information that is explicitly present in the text.\n\n"
        "Return ONLY valid JSON with keys: name (string), tagline (string), core_features (array, max 6), "
        "target_market (string), pricing_model (string), strengths (array, max 3), "
        "weaknesses (array, max 3), evidence (object).\n\n"
        "CRITICAL RULES:\n"
        "- If pricing is not mentioned anywhere in the text, set pricing_model to "
        "'Not stated on website' -- do NOT guess or use generic phrases like 'subscription-based' "
        "unless those exact words or clear pricing numbers appear in the text.\n"
        "- Weaknesses must be inferred ONLY from what's absent or limited based on the actual "
        "feature list you extracted -- not generic SaaS weaknesses. If you cannot reasonably infer "
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


def extract_competitor_profile_tier1(
    url: str, content: str, idea_text: str, parsed_idea: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    CHANGE 1: Tier 1 extraction for named/listicle candidates.
    Skips the separate classify+relevance Groq call. Instead, embeds a lightweight
    relevance check directly into the extraction prompt — costs the same tokens as
    a plain extraction call but recovers the separate classify+relevance call.
    Returns None if the model determines the product is clearly not relevant.
    """
    key_features = parsed_idea.get('key_features', [])
    system_prompt = (
        "You are a competitive intelligence analyst. You will be given raw text scraped from "
        "a named product's website that we have reason to believe is a competitor.\n\n"
        "First, quickly determine: does this page describe a real software product that serves "
        "the same core job as the user's idea? If the page is clearly not a software product "
        "homepage (e.g. it is a blog, news article, social profile, app-store listing, or "
        "an entirely unrelated product), return ONLY: {\"is_relevant\": false}.\n\n"
        "If it IS a plausible competitor (a software product with any overlap to the user's idea), "
        "extract the full profile AND set is_relevant to true.\n\n"
        "Return ONLY valid JSON with keys: is_relevant (boolean), name (string), tagline (string), "
        "core_features (array, max 6), target_market (string), pricing_model (string), "
        "strengths (array, max 3), weaknesses (array, max 3), evidence (object).\n\n"
        "CRITICAL RULES:\n"
        "- If pricing is not mentioned anywhere in the text, set pricing_model to "
        "'Not stated on website'.\n"
        "- Weaknesses must be inferred ONLY from what's absent or limited based on the actual "
        "feature list you extracted — not generic SaaS weaknesses.\n"
        "- core_features must be features explicitly described in the text.\n"
        "- Every value must be traceable to a specific phrase in the provided text.\n"
        "- The 'evidence' field maps each of the 7 profile keys to the exact short snippet "
        "(max 15 words) from the source text that justifies that value."
    )

    idea_context = (
        f"USER'S STARTUP IDEA:\n{idea_text}\n\n"
        f"Key Features of the idea:\n"
        + "\n".join(f"  - {f}" for f in key_features)
        + f"\n\nProduct URL: {url}\n\nContent (first 6000 chars):\n{content[:6000]}"
    )

    try:
        result = call_groq_json(system_prompt, idea_context)
        if not result.get("is_relevant", True):  # default True so missing key doesn't drop valid results
            return None
        result["url"] = url
        return result
    except Exception as e:
        print(f"Tier 1 extraction failed for {url}: {e}")
        return None


def check_relevance(idea_text: str, parsed_idea: Dict[str, Any], content: str) -> Dict[str, Any]:
    """
    Tightened relevance gate: requires job-level overlap, not category-level overlap.
    First classifies entity_type (software vs non-software), then checks whether
    the candidate shares >=2 of the user's key_features as primary functions.
    Returns {entity_type: str, is_relevant: bool, relevance_reason: str}.
    FIX 2: Re-raises API errors instead of defaulting to 'not relevant'.
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
        "Step 1 -- Determine entity_type from the actual page content. If "
        "entity_type is anything other than 'software_product', set "
        "is_relevant to false automatically, regardless of keyword overlap. "
        "Training providers, courses, certifications, agencies, and "
        "consultancies are NEVER relevant competitors to a software idea, "
        "even if their marketing copy uses words like 'AI,' 'platform,' or "
        "'revolutionize.'\n\n"
        "Step 2 -- If entity_type is 'software_product', evaluate JOB-LEVEL "
        "overlap, not category-level overlap. Ask: 'If a user adopted this "
        "candidate product instead of the user's idea, would it actually "
        "solve the same core job?'\n\n"
        "Use this test: compare the candidate's core function against the "
        "user's key_features list. is_relevant is true ONLY if the candidate "
        "shares at least 2 of the user's specific key_features as a primary "
        "function (not a minor/secondary feature buried in a larger suite).\n\n"
        "Clarification for Large Broad Platforms:\n"
        "If the candidate is a large, multi-feature platform (e.g. an all-in-one project management or productivity suite), "
        "check whether the SPECIFIC feature matching the user's key_features is explicitly marketed as a current, named feature "
        "on the page (not assumed) -- even if it is one of many features listed, not the platform's sole focus. "
        "Do not penalize relevance for a tool that does many things, as long as at least one explicitly described feature "
        "directly matches at least one of the user's key_features (lower the previous '2 features' threshold to '1 explicit, named feature' "
        "when the candidate is a known broad-platform type, since broad platforms naturally dilute any single feature's "
        "prominence in their own marketing copy).\n\n"
        "Examples of what must be marked false even though they sound related:\n"
        "- A general note-taking app, when the idea is specifically about "
        "AI-driven task prioritization -- note-taking is not the same job.\n"
        "- A time-tracking tool, when the idea is about task prioritization "
        "and habit learning -- time tracking is a different job.\n"
        "- A video conferencing/chat platform, when the idea is a task "
        "management app -- communication is not task management, even if "
        "the platform has a task list as a minor feature.\n"
        "- A broad all-in-one workspace (notes + docs + wiki + tasks), unless "
        "its task-management capability specifically matches the user's "
        "key_features (applying the broad platform rules above).\n\n"
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
        f"Key Features (candidate MUST match at least 2 features as primary functions, or at least 1 explicit named feature if it is a large multi-feature platform):\n"
        + "\n".join(f"  - {f}" for f in key_features)
        + f"\n\nCANDIDATE PRODUCT PAGE CONTENT (first 5000 chars):\n{content[:5000]}"
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
    except HTTPException:
        raise  # FIX 2: propagate API errors for retry queue
    except Exception as e:
        if _is_rate_limit_error(str(e)):
            raise  # FIX 2: propagate rate-limit errors
        print(f"Relevance check failed: {e}")
        return {"entity_type": "other_non_product", "is_relevant": False, "relevance_reason": f"Relevance check failed: {e}"}


# =====================================================================
# Async parallel-processing helpers (rewritten for FIX 1, 2, 3.2)
# =====================================================================

async def classify_and_check_relevance_async(idea_text: str, parsed_idea: Dict[str, Any], content: str) -> Dict[str, Any]:
    """Throttled async wrapper for the merged classify + relevance call."""
    async with _groq_throttle:
        return await asyncio.to_thread(classify_and_check_relevance, idea_text, parsed_idea, content)


async def check_relevance_async(idea_text: str, parsed_idea: Dict[str, Any], content: str) -> Dict[str, Any]:
    """Throttled async wrapper for standalone relevance check."""
    async with _groq_throttle:
        return await asyncio.to_thread(check_relevance, idea_text, parsed_idea, content)


async def extract_competitor_profile_async(url: str, content: str) -> Optional[Dict[str, Any]]:
    """Throttled async wrapper for competitor profile extraction (Tier 2)."""
    async with _groq_throttle:
        return await asyncio.to_thread(extract_competitor_profile, url, content)


async def extract_competitor_profile_tier1_async(
    url: str, content: str, idea_text: str, parsed_idea: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """CHANGE 1: Throttled async wrapper for Tier 1 (named/listicle) extraction."""
    async with _groq_throttle:
        return await asyncio.to_thread(extract_competitor_profile_tier1, url, content, idea_text, parsed_idea)


async def process_candidate(url: str, idea_text: str, parsed_idea: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process a single candidate URL: fetch -> classify+relevance (merged, single LLM call).
    FIX 1: No outer semaphore -- throttling is centralized in GroqThrottle.
    FIX 2: API errors return 'unverified_api_error' instead of dropping.
    FIX 3.2: Uses merged classify_and_check_relevance (1 call instead of 2).
    """
    print(f"  Fetching & classifying: {url}")
    content = await fetch_jina_content_async(url)
    if content is None:
        print(f"    -> dead (fetch failed or < 200 chars)")
        return {"status": "dead", "url": url}

    try:
        result = await classify_and_check_relevance_async(idea_text, parsed_idea, content)
    except Exception as e:
        error_str = str(e)
        if isinstance(e, HTTPException) or _is_rate_limit_error(error_str):
            print(f"    -> API error, marking unverified: {error_str[:120]}")
            return {"status": "unverified_api_error", "url": url, "content": content}
        print(f"    -> classification error: {e}")
        return {"status": "irrelevant", "url": url}

    page_type = result.get("page_type", "irrelevant")

    if page_type == "product_homepage":
        pname = result.get("product_name") or "Unknown"
        is_relevant = bool(result.get("is_relevant", False))
        entity_type = result.get("entity_type", "other_non_product")
        reason = result.get("relevance_reason", "")

        if not is_relevant:
            print(f"    -> DROPPED [{entity_type}]: {reason}")
            return {"status": "dropped", "url": url, "product_name": pname}

        print(f"    -> RELEVANT [{entity_type}]: {reason}")
        return {
            "status": "relevant",
            "url": url,
            "content": content,
            "product_name": pname,
        }
    elif page_type == "listicle_or_review_site":
        mentioned = result.get("mentioned_products", [])
        print(f"    -> listicle, mentioned products: {mentioned}")
        return {"status": "listicle", "url": url, "mentioned_products": mentioned}
    else:
        print(f"    -> irrelevant, dropping")
        return {"status": "irrelevant", "url": url}


async def process_listicle_followup(
    product_name: str, idea_text: str, parsed_idea: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """
    CHANGE 1 (Tier 1): Process a listicle-mentioned product.
    Skips classify+relevance Groq call entirely. Instead:
    1. Searches for the official site.
    2. Checks domain blocklist (already handled inside fetch_jina_content_async).
    3. Fetches via Jina.
    4. Passes directly to Tier 1 extraction (relevance embedded in extraction prompt).
    """
    print(f"  [Tier1/Listicle] Searching for official site: {product_name}")
    followup_results = await search_competitors_async(f"{product_name} official website")
    if not followup_results:
        return None

    # Try search results in order, skip blocked domains
    for top_result in followup_results[:3]:
        fu_url = top_result.get("url", "")
        if not fu_url:
            continue

        # CHANGE 2: Blocklist check is inside fetch_jina_content_async, but log it here too
        if is_blocked_domain(fu_url):
            print(f"  [Tier1/Listicle] Skipping blocked domain for {product_name}: {fu_url}")
            continue

        fu_domain = extract_domain(fu_url)
        fu_content = await fetch_jina_content_async(fu_url)
        if fu_content is None:
            print(f"  [Tier1/Listicle] Dead page for {product_name}: {fu_url}")
            continue

        # CHANGE 1: Rule-based short-content check (< 300 chars = dead)
        if len(fu_content) < 300:
            print(f"  [Tier1/Listicle] Content too short for {product_name}: {fu_url}")
            continue

        # Pass directly to Tier 1 extraction (no classify+relevance call)
        try:
            profile = await extract_competitor_profile_tier1_async(fu_url, fu_content, idea_text, parsed_idea)
            if profile is None:
                print(f"  [Tier1/Listicle] DROPPED by extraction (not relevant): {product_name} @ {fu_url}")
                return None  # extraction said not relevant — don't try next URL
            pname = profile.get("name") or product_name
            print(f"  [Tier1/Listicle] ACCEPTED: {pname} @ {fu_url}")
            return {
                "url": fu_url,
                "domain": fu_domain,
                "content": fu_content,
                "product_name": pname,
                "_tier1_profile": profile,  # carry pre-extracted profile forward
            }
        except Exception as e:
            print(f"  [Tier1/Listicle] Extraction error for {product_name} @ {fu_url}: {e}")
            return None

    print(f"  [Tier1/Listicle] No suitable URL found for {product_name}")
    return None


async def verify_named_competitor(name: str, idea_text: str, parsed_idea: Dict[str, Any]) -> Dict[str, Any]:
    """
    CHANGE 1 (Tier 1): Verifies a specific named competitor from likely_known_competitors.
    No classify+relevance Groq call. Path:
      Search -> domain blocklist check -> Jina fetch -> short-content check
      -> Tier 1 extraction (relevance embedded, one Groq call total per candidate).
    """
    print(f"  [Tier1/Named] Starting verification for '{name}'")
    try:
        query = f"{name} software"
        results = await search_competitors_async(query)
        if not results:
            reason = f"No search results returned for query '{query}'"
            print(f"  [Tier1/Named] FAILED: '{name}' at search. Reason: {reason}")
            return {"status": "failed", "name": name, "stage": "search", "reason": reason}
    except Exception as e:
        reason = f"Search failed with exception: {str(e)}"
        print(f"  [Tier1/Named] FAILED: '{name}' at search. Reason: {reason}")
        return {"status": "failed", "name": name, "stage": "search", "reason": reason}

    last_failure = None
    for idx, r in enumerate(results):
        url = r.get("url", "")
        print(f"  [Tier1/Named] Trying result {idx + 1} for '{name}': {url}")

        # CHANGE 2: Use global BLOCKED_DOMAINS set (more comprehensive than old local list)
        if is_blocked_domain(url):
            last_failure = {
                "status": "failed", "name": name, "stage": "classify",
                "reason": f"Skipped blocked domain: {url}", "url": url
            }
            print(f"  [Tier1/Named] Skipping blocked domain: {url}")
            continue

        # Fetch (CHANGE 2: blocklist also checked inside, but skipped here since we already checked)
        try:
            content = await fetch_jina_content_async(url)
            if not content:
                last_failure = {
                    "status": "failed", "name": name, "stage": "fetch",
                    "reason": f"Failed to fetch page content via Jina from URL: {url}", "url": url
                }
                continue
        except Exception as e:
            last_failure = {
                "status": "failed", "name": name, "stage": "fetch",
                "reason": f"Fetch failed: {str(e)} from URL: {url}", "url": url
            }
            continue

        # CHANGE 1: Rule-based short-content check (< 300 chars = dead, no Groq)
        if len(content) < 300:
            last_failure = {
                "status": "failed", "name": name, "stage": "fetch",
                "reason": f"Content too short ({len(content)} chars): {url}", "url": url
            }
            print(f"  [Tier1/Named] Content too short for '{name}' at {url}")
            continue

        # CHANGE 1: Skip classify+relevance — go straight to Tier 1 extraction
        try:
            profile = await extract_competitor_profile_tier1_async(url, content, idea_text, parsed_idea)
            if profile is None:
                # Extraction decided it's not relevant
                last_failure = {
                    "status": "failed", "name": name, "stage": "relevance",
                    "reason": f"Tier 1 extraction: product not relevant to idea from URL: {url}", "url": url
                }
                print(f"  [Tier1/Named] DROPPED by extraction (not relevant): '{name}' @ {url}")
                # Don't try more URLs if the found page is clearly the product but not relevant
                break
            pname = profile.get("name") or name
            print(f"  [Tier1/Named] SUCCESS: '{name}' verified as '{pname}' at {url}")
            return {
                "status": "success",
                "name": name,
                "product_name": pname,
                "url": url,
                "content": content,
                "_tier1_profile": profile,  # carry pre-extracted profile to skip Agent 3
            }
        except Exception as e:
            last_failure = {
                "status": "failed", "name": name, "stage": "relevance",
                "reason": f"Tier 1 extraction exception: {str(e)} from URL: {url}", "url": url
            }
            continue

    if last_failure:
        print(f"  [Tier1/Named] FAILED: All candidate URLs failed for '{name}'. Last: {last_failure['reason']}")
        return last_failure
    reason = f"No suitable homepage URL found in search results for '{name}'"
    print(f"  [Tier1/Named] FAILED: '{name}'. Reason: {reason}")
    return {"status": "failed", "name": name, "stage": "classify", "reason": reason}


# =====================================================================
# Core analysis pipeline
# =====================================================================

async def run_analysis(idea_text: str, progress_callback=None) -> Dict[str, Any]:
    """
    Core analysis pipeline. Runs Agents 1-4 with parallel candidate processing.
    progress_callback(step, label, detail) is awaited at each major stage for SSE streaming.
    """

    async def emit(step: int, label: str, detail: str = ""):
        if progress_callback:
            await progress_callback(step, label, detail)

    print(f"Starting analysis for idea: {idea_text[:60]}...")

    # =========================================================================
    # AGENT 1: Idea Parser (Fix A -- added industry_terms + strengthened likely_known_competitors)
    # =========================================================================
    await emit(0, "Parsing your idea...")
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
        "- likely_known_competitors: array of up to 3 REAL product names you already know of "
        "that solve a similar problem (maximum 3, empty array if genuinely none come to mind -- do not invent names). "
        "These will be directly verified via web search, so accuracy matters -- only include names "
        "you are confident are real products.\n"
        "- industry_terms: array of 3-5 terms. CHANGE 3: Use the specific vocabulary that professionals "
        "and products in this domain actually use in their marketing and product descriptions -- not the "
        "user's layperson phrasing. For example, if the user says 'AI documentation from code comments,' "
        "the industry terms might be 'docs-as-code,' 'OpenAPI generator,' 'developer portal,' "
        "'API reference automation.' Always include at least one term that a product in this space "
        "would use on their own homepage.\n"
        "- search_queries: array of 4 distinct search query strings designed to find REAL competing "
        "products. Include at least one query using a known competitor name (e.g. 'alternatives to "
        "Grammarly for business email tone'), one category-based query, and one feature-specific query. "
        "Each search query must include at least one concrete term from the user's specific idea "
        "(a feature, a target platform, or the exact problem) -- not just generic category words like "
        "'AI tool' or 'productivity app' alone. A query must be specific enough that an unrelated "
        "product in a different category would not plausibly rank for it.\n\n"
        "Return ONLY valid JSON, no markdown fences."
    )
    parsed_idea = await call_groq_json_async(agent1_prompt, idea_text)
    print("Parsed Idea:", json.dumps(parsed_idea, indent=2)[:500])

    # FIX 6: Granular progress after Agent 1
    n_features = len(parsed_idea.get('key_features', []))
    likely_known = parsed_idea.get("likely_known_competitors", [])[:3]  # hard cap at 3
    await emit(0, "Idea parsed", f"{n_features} features, {len(likely_known)} named competitors identified")

    # =========================================================================
    # AGENT 2: Search & Discovery (Fix A -- direct verification + industry-term queries)
    # =========================================================================
    print("Running Agent 2: Search & Discovery...")
    search_queries = parsed_idea.get("search_queries", [])
    industry_terms = parsed_idea.get("industry_terms", [])

    if not search_queries:
        fallback_query = (
            f"{parsed_idea.get('solution', '')} {parsed_idea.get('domain', '')} competitors alternatives"
        )
        search_queries = [fallback_query]

    # Industry-term queries intentionally removed to reduce downstream Groq call count.
    # Agent 1's 4 hand-crafted queries are already specific enough.

    total_queries = len(search_queries)
    print(f"  Running verification for {len(likely_known)} named competitors and {total_queries} general search queries.")
    await emit(1, "Searching for competitors...", f"Verifying {len(likely_known)} named, running {total_queries} general queries")

    # Run verification tasks and general searches in parallel
    verification_tasks = [
        verify_named_competitor(name, idea_text, parsed_idea)
        for name in likely_known
    ]
    general_search_tasks = [
        search_competitors_async(q)
        for q in search_queries
    ]

    verification_results, general_search_results = await asyncio.gather(
        asyncio.gather(*verification_tasks, return_exceptions=True),
        asyncio.gather(*general_search_tasks, return_exceptions=True)
    )

    product_homepages = []          # list of {url, content, product_name}
    covered_product_names = set()   # product names already covered by homepage results
    seen_domains = set()
    verification_failures = []

    # Process verification results
    for i, res in enumerate(verification_results):
        name = likely_known[i]
        if isinstance(res, Exception):
            err_reason = f"Verification task crashed with exception: {str(res)}"
            print(f"  [Verification Fail] Name: {name}, Reason: {err_reason}")
            verification_failures.append({
                "name": name,
                "stage": "verify",
                "reason": err_reason
            })
            continue

        if res.get("status") == "success":
            pname = res["product_name"]
            url = res["url"]
            domain = extract_domain(url)

            product_homepages.append({
                "url": url,
                "content": res["content"],
                "product_name": pname,
            })
            covered_product_names.add(pname.lower().strip())
            if domain:
                seen_domains.add(domain)
        else:
            verification_failures.append({
                "name": res["name"],
                "stage": res["stage"],
                "reason": res["reason"]
            })

    # Process general search queries
    all_results = []
    for i, results in enumerate(general_search_results):
        if isinstance(results, Exception):
            print(f"  General search query {i} failed: {results}")
            continue
        query_label = search_queries[i] if i < len(search_queries) else "unknown"
        print(f"  Tavily search: {query_label[:60]} -> {len(results)} results")
        for r in results:
            url = r.get("url", "")
            domain = extract_domain(url)
            if domain and domain not in seen_domains:
                seen_domains.add(domain)
                all_results.append(r)

    # CHANGE 1: Hard cap at MAX_CANDIDATES (5) for Tier 2 general search URLs
    if len(all_results) > MAX_CANDIDATES:
        print(f"  Capping Tier 2 general candidates from {len(all_results)} to {MAX_CANDIDATES}")
        all_results = all_results[:MAX_CANDIDATES]

    print(
        f"Agent 2: {len(all_results)} Tier 2 candidate URLs (general search), "
        f"{len(product_homepages)} Tier 1 named competitors already verified, "
        f"across {total_queries} queries."
    )

    # FIX 6: Granular progress after search
    await emit(1, "Search complete", f"{len(all_results)} candidate URLs found, {len(product_homepages)} named verified")

    # =========================================================================
    # CHANGE 1: Tier 2 Source Classifier — general search URLs only
    # Named/listicle candidates are Tier 1 and use extract_competitor_profile_tier1 directly.
    # =========================================================================
    await emit(2, "Classifying sources...", f"Processing {len(all_results)} Tier 2 candidates")
    print("Running Tier 2 Source Classifier (classify+relevance for general search URLs)...")

    # Phase 1: Process Tier 2 (general search) candidates in parallel
    # These still use the full classify+relevance Groq call (unknown URLs).
    candidate_tasks = []
    for result in all_results:
        url = result.get("url", "")
        if url:
            candidate_tasks.append(process_candidate(url, idea_text, parsed_idea))

    phase1_results = await asyncio.gather(*candidate_tasks, return_exceptions=True)

    listicle_mention_counts = Counter()  # count frequency for capping
    retry_queue = []                     # collect unverified candidates for retry

    for result in phase1_results:
        if isinstance(result, Exception):
            print(f"  Candidate processing error: {result}")
            continue
        if result["status"] == "relevant":
            pname = result["product_name"]
            url = result["url"]
            if pname.lower().strip() not in covered_product_names:
                product_homepages.append({
                    "url": url,
                    "content": result["content"],
                    "product_name": pname,
                })
                covered_product_names.add(pname.lower().strip())
        elif result["status"] == "listicle":
            for name in result.get("mentioned_products", []):
                if name and name.lower().strip() not in covered_product_names:
                    listicle_mention_counts[name.strip()] += 1
        elif result["status"] == "unverified_api_error":
            retry_queue.append(result)

    n_relevant = sum(1 for r in phase1_results if not isinstance(r, Exception) and r.get("status") == "relevant")
    n_unverified = len(retry_queue)
    await emit(2, "Tier 2 classified", f"{n_relevant} relevant from general search, {n_unverified} pending retry")

    # Phase 2 (Tier 1): Follow up on listicle-mentioned products — NO classify+relevance Groq call
    # CHANGE 1: process_listicle_followup now uses Tier 1 extraction path.
    to_followup = sorted(
        listicle_mention_counts.keys(),
        key=lambda n: (-listicle_mention_counts[n], n.lower())
    )
    to_followup = [
        name for name in to_followup
        if name.lower().strip() not in covered_product_names
    ][:8]  # cap at 8 (Tier 1 is cheaper — no separate classify+relevance call)

    if to_followup:
        print(
            f"Tier 1 Phase 2: Following up on "
            f"{len(to_followup)} listicle-mentioned products (Tier 1, capped at 8)..."
        )
        await emit(2, "Following up listicle mentions...", f"{len(to_followup)} Tier 1 names to check")

        followup_tasks = [
            process_listicle_followup(name, idea_text, parsed_idea)
            for name in to_followup
        ]
        followup_results = await asyncio.gather(*followup_tasks, return_exceptions=True)

        for result in followup_results:
            if isinstance(result, Exception):
                print(f"  Followup error: {result}")
                continue
            if result is None:
                continue
            # Tier 1 followup does not produce unverified_api_error (extraction handles errors internally)
            # Deduplicate by domain after parallel gathering
            if result.get("domain") and result["domain"] not in seen_domains:
                seen_domains.add(result["domain"])
                pname = result["product_name"]
                if pname.lower().strip() not in covered_product_names:
                    product_homepages.append({
                        "url": result["url"],
                        "content": result["content"],
                        "product_name": pname,
                        "_tier1_profile": result.get("_tier1_profile"),  # carry pre-extracted profile
                    })
                    covered_product_names.add(pname.lower().strip())

    # =========================================================================
    # FIX 2: Retry queue -- sequential retry after 20s cooldown
    # =========================================================================
    if retry_queue:
        print(f"  Retry queue: {len(retry_queue)} candidates to retry after 20s cooldown...")
        await emit(2, "Retrying unverified candidates...", f"{len(retry_queue)} to retry after cooldown")
        await asyncio.sleep(20)

        recovered = 0
        for item in retry_queue:
            url = item.get("url", "")
            content = item.get("content", "")
            if not content:
                print(f"    [Retry] No content saved for {url}, skipping")
                continue
            try:
                result = await classify_and_check_relevance_async(idea_text, parsed_idea, content)
                page_type = result.get("page_type", "irrelevant")
                if page_type == "product_homepage":
                    pname = result.get("product_name") or "Unknown"
                    is_relevant = bool(result.get("is_relevant", False))
                    if is_relevant and pname.lower().strip() not in covered_product_names:
                        product_homepages.append({
                            "url": url,
                            "content": content,
                            "product_name": pname,
                        })
                        covered_product_names.add(pname.lower().strip())
                        domain = extract_domain(url)
                        if domain:
                            seen_domains.add(domain)
                        recovered += 1
                        print(f"    [Retry] RECOVERED: {pname} at {url}")
                    else:
                        print(f"    [Retry] Still irrelevant: {pname} ({result.get('relevance_reason', '')})")
                elif page_type == "listicle_or_review_site":
                    print(f"    [Retry] Listicle, skipping: {url}")
                else:
                    print(f"    [Retry] Irrelevant: {url}")
            except Exception as e:
                print(f"    [Retry] Failed again: {url} ({e})")

        print(f"  Retry complete: {recovered} candidates recovered out of {len(retry_queue)}")

    # FIX 6: Final classification summary
    await emit(2, "Classification complete", f"{len(product_homepages)} relevant competitors confirmed")

    print(
        f"Source Classifier complete: {len(product_homepages)} relevant software "
        f"competitors (after strict job-level filtering)."
    )

    # =========================================================================
    # AGENT 3: Competitor Intelligence -- parallel extraction
    # CHANGE 1: Tier 1 candidates carry a pre-extracted profile (_tier1_profile).
    # Re-use it directly; only run extraction for Tier 2 candidates (no _tier1_profile).
    # =========================================================================
    tier1_with_profile = [hp for hp in product_homepages if hp.get("_tier1_profile")]
    tier2_need_extraction = [hp for hp in product_homepages if not hp.get("_tier1_profile")]

    await emit(
        3, "Analyzing competitor products...",
        f"{len(tier1_with_profile)} Tier 1 profiles ready, extracting {len(tier2_need_extraction)} Tier 2"
    )
    print(
        f"Running Agent 3: {len(tier1_with_profile)} Tier 1 profiles reused, "
        f"{len(tier2_need_extraction)} Tier 2 profiles to extract..."
    )

    extraction_tasks = [
        extract_competitor_profile_async(hp["url"], hp["content"])
        for hp in tier2_need_extraction
    ]
    extraction_results = await asyncio.gather(*extraction_tasks, return_exceptions=True)

    competitors = []

    # Add Tier 1 pre-extracted profiles directly
    for hp in tier1_with_profile:
        profile = hp["_tier1_profile"]
        # Ensure url field is present
        profile.setdefault("url", hp["url"])
        competitors.append(profile)
        print(f"  [Tier1] Reused profile: {profile.get('name', hp.get('product_name', 'Unknown'))}")

    # Add Tier 2 freshly extracted profiles
    for i, result in enumerate(extraction_results):
        if isinstance(result, Exception):
            print(f"  Extraction error for {tier2_need_extraction[i]['url']}: {result}")
            continue
        if result is not None:
            competitors.append(result)
            print(f"  [Tier2] Extracted profile: {result.get('name', 'Unknown')}")

    print(f"Agent 3 complete: {len(competitors)} competitor profiles extracted.")

    # FIX 6: Progress after extraction
    await emit(3, "Profiles extracted", f"{len(competitors)} competitor profiles ready")

    # =========================================================================
    # AGENT 4: Strategy Agent (FIX 5: decoupled competitor references)
    # =========================================================================
    await emit(4, "Generating strategy...")
    print("Running Agent 4: Strategy Agent...")

    # FIX 5: Strip fields that could leak names into Agent 4
    idea_for_strategy = {
        k: v for k, v in parsed_idea.items()
        if k not in ("likely_known_competitors", "industry_terms", "search_queries")
    }

    agent4_prompt = (
        "You are a startup strategist. Compare this idea against the verified competitor profiles provided. "
        "Return ONLY valid JSON with keys:\n"
        "- feature_matrix: object where each key is a feature name (8 features total), "
        "value is object with keys: user_idea (boolean), and one key per competitor name (boolean)\n"
        "- idea_scores: object with keys innovation, market_gap, feasibility, competition_density -- each a number 0-100\n"
        "- competitor_scores: array of objects, each with name and scores object (same 4 keys, each 0-100)\n"
        "- suggestions: array of 5 objects each with keys: title (string), description (string), priority (High/Medium/Low)\n"
        "- summary: 2 sentence executive summary string\n\n"
        "Base feature_matrix and comparison entirely on the core_features arrays provided for each "
        "competitor -- do not assume a competitor has a feature unless it appears in their core_features list.\n\n"
        "You must reference competitors ONLY by the exact names present in the competitor_profiles list provided to you in this prompt. "
        "Never reference likely_known_competitors, parsed_idea fields, or any name from earlier pipeline stages "
        "that is not in the final competitor_profiles list. "
        "Do not name, imply, or reference any product, company, or tool that is not in the competitor_profiles list, "
        "even if you recognize it as a real and relevant competitor from general knowledge. "
        "If the competitor_profiles list is empty, your summary and verdict must describe the result based solely "
        "on the idea's characteristics and explicitly state that no verified competitors were found in this analysis -- "
        "do not name any companies in that case."
    )

    strategy_payload = {"idea": idea_for_strategy, "competitor_profiles": competitors}
    strategy = await call_groq_json_async(agent4_prompt, json.dumps(strategy_payload))
    print("Strategy summary:", strategy.get("summary"))

    return {
        "parsed_idea": parsed_idea,
        "competitors": competitors,
        "strategy": strategy,
        "verification_failures": verification_failures,
    }


# =====================================================================
# API Endpoints
# =====================================================================

@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/api/analyze")
async def analyze_idea(request: AnalyzeRequest):
    """Standard JSON response endpoint (backward compatible)."""
    if not request.idea.strip():
        raise HTTPException(status_code=400, detail="Idea text cannot be empty.")
    return await run_analysis(request.idea)


@app.post("/api/analyze-stream")
async def analyze_idea_stream(request: AnalyzeRequest):
    """
    SSE streaming endpoint. Emits real-time progress events as the pipeline runs,
    followed by a final result event containing the full analysis.
    """
    if not request.idea.strip():
        raise HTTPException(status_code=400, detail="Idea text cannot be empty.")

    queue: asyncio.Queue = asyncio.Queue()

    async def progress_callback(step: int, label: str, detail: str = ""):
        await queue.put({"type": "step", "step": step, "label": label, "detail": detail})

    async def run_in_background():
        try:
            result = await run_analysis(request.idea, progress_callback)
            await queue.put({"type": "result", "data": result})
        except HTTPException as e:
            await queue.put({"type": "error", "message": e.detail})
        except Exception as e:
            await queue.put({"type": "error", "message": str(e)})
        # Sentinel to signal end of stream
        await queue.put(None)

    async def event_generator():
        task = asyncio.create_task(run_in_background())
        while True:
            item = await queue.get()
            if item is None:
                break
            yield f"data: {json.dumps(item)}\n\n"
        await task

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

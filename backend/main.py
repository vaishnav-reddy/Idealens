import os
import re
import json
import requests
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import OpenAI
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

# Initialize OpenAI client with xAI base URL
GROK_API_KEY = os.getenv("GROK_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

# Create a client instance
# Since we need to handle cases where keys are set dynamically or are missing during initial startup,
# we initialize it inside functions or lazily.
def get_grok_client() -> OpenAI:
    api_key = os.getenv("GROK_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GROK_API_KEY environment variable is not set.")
    return OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")


class AnalyzeRequest(BaseModel):
    idea: str

def clean_and_parse_json(text: str) -> Dict[str, Any]:
    """
    Cleans markdown code fences (e.g. ```json ... ```) from the LLM output
    and parses it into a Python dictionary.
    """
    cleaned = text.strip()
    # Remove markdown code block wrappers
    if cleaned.startswith("```"):
        # Match anything between triple backticks (with optional json identifier)
        match = re.search(r"^(?:```(?:json)?\n)?(.*?)(?:\n```)?$", cleaned, re.DOTALL)
        if match:
            cleaned = match.group(1).strip()
    
    # Try direct parsing
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        # Fallback: try to find the first '{' and last '}'
        json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass
        raise ValueError(f"Failed to parse JSON response: {text[:200]}...") from e

def call_grok_json(system_prompt: str, user_content: str) -> Dict[str, Any]:
    """
    Helper to call the xAI Grok API and enforce JSON output.
    """
    client = get_grok_client()
    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            temperature=0.2,
            response_format={"type": "json_object"}
        )
        content = response.choices[0].message.content
        return clean_and_parse_json(content)
    except Exception as e:
        # If response_format is not supported or fails, retry without it but asking for JSON in prompt
        print(f"Grok API call with response_format=json failed: {e}. Retrying without response_format.")
        try:
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system_prompt + "\nIMPORTANT: You must return ONLY raw JSON. Do not wrap in markdown or markdown code blocks."},
                    {"role": "user", "content": user_content}
                ],
                temperature=0.2
            )
            content = response.choices[0].message.content
            return clean_and_parse_json(content)
        except Exception as retry_err:
            raise HTTPException(status_code=500, detail=f"Grok API Call failed: {str(retry_err)}")

def search_competitors(query: str) -> List[Dict[str, Any]]:
    """
    Searches for competitors using Tavily API, with fallback to direct HTTP post request.
    """
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        print("Warning: TAVILY_API_KEY environment variable is not set. Returning empty list.")
        return []

    try:
        from tavily import TavilyClient
        tavily_client = TavilyClient(api_key=api_key)
        response = tavily_client.search(query=query, max_results=5)
        return response.get("results", [])
    except Exception as e:
        print(f"Error using Tavily SDK: {e}. Falling back to direct REST request.")
        try:
            res = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "max_results": 5,
                    "search_depth": "basic"
                },
                timeout=12
            )
            res.raise_for_status()
            return res.json().get("results", [])
        except Exception as req_err:
            print(f"Failed to fetch from Tavily via REST API: {req_err}")
            return []

def fetch_and_analyze_competitor(url: str, title: str) -> Optional[Dict[str, Any]]:
    """
    Fetches the cleaned markdown of a competitor URL using Jina Reader
    and analyzes it using Grok.
    """
    try:
        jina_url = f"https://r.jina.ai/{url}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        # Fetch clean content
        res = requests.get(jina_url, headers=headers, timeout=15)
        res.raise_for_status()
        content = res.text[:3000]
    except Exception as e:
        print(f"Failed to fetch content from Jina Reader for URL {url}: {e}")
        # Skip gracefully
        return None

    # Call Grok Agent 3
    system_prompt = (
        "You are a competitive intelligence analyst. Analyze this product. "
        "Return ONLY valid JSON with keys: name (string), tagline (string), "
        "core_features (array of 6 strings), target_market (string), pricing_model (string), "
        "strengths (array of 3 strings), weaknesses (array of 3 strings)"
    )
    user_content = f"Product Title: {title}\nProduct URL: {url}\n\nContent:\n{content}"
    
    try:
        profile = call_grok_json(system_prompt, user_content)
        profile["url"] = url
        return profile
    except Exception as e:
        print(f"Failed to analyze competitor {url} with Grok: {e}")
        return None

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.post("/api/analyze")
def analyze_idea(request: AnalyzeRequest):
    if not request.idea.strip():
        raise HTTPException(status_code=400, detail="Idea text cannot be empty.")

    print(f"Starting analysis for idea: {request.idea[:60]}...")

    # --- AGENT 1: Idea Parser ---
    print("Running Agent 1: Idea Parser...")
    agent1_prompt = (
        "You are a startup analyst. Extract structured information from this idea. "
        "Return ONLY valid JSON with these exact keys: problem (string), solution (string), "
        "domain (string), target_users (string), key_features (array of 5 strings), unique_angle (string)"
    )
    parsed_idea = call_grok_json(agent1_prompt, request.idea)
    print("Parsed Idea:", parsed_idea)

    # --- AGENT 2: Search & Discovery ---
    print("Running Agent 2: Search & Discovery...")
    query = f"{parsed_idea.get('solution', '')} {parsed_idea.get('domain', '')} competitors alternatives"
    print(f"Searching Tavily for query: {query}")
    tavily_results = search_competitors(query)
    print(f"Tavily returned {len(tavily_results)} results.")

    # --- AGENT 3: Competitor Intelligence ---
    print("Running Agent 3: Competitor Intelligence...")
    competitors = []
    # Take top 3 URLs from search results
    top_results = tavily_results[:3]
    for result in top_results:
        url = result.get("url")
        title = result.get("title", "Unknown Competitor")
        if not url:
            continue
        print(f"Analyzing competitor URL: {url}")
        profile = fetch_and_analyze_competitor(url, title)
        if profile:
            competitors.append(profile)

    # Fallback if no competitors were successfully analyzed
    if not competitors:
        print("Warning: No competitors successfully parsed. Creating high-quality placeholder competitor data from search results to ensure dashboard renders properly.")
        # If Jina Reader failed for all, generate profiles based on Tavily snippets directly
        for idx, result in enumerate(tavily_results[:3]):
            url = result.get("url", "https://example.com")
            title = result.get("title", f"Competitor {idx+1}")
            snippet = result.get("content", "Startup offering related solutions.")
            
            system_prompt = (
                "You are a competitive intelligence analyst. You are given a competitor snippet. "
                "Synthesize a competitor profile. Return ONLY valid JSON with keys: "
                "name (string), tagline (string), core_features (array of 6 strings), target_market (string), "
                "pricing_model (string), strengths (array of 3 strings), weaknesses (array of 3 strings)"
            )
            user_content = f"Product Title: {title}\nProduct URL: {url}\nSnippet:\n{snippet}"
            try:
                profile = call_grok_json(system_prompt, user_content)
                profile["url"] = url
                competitors.append(profile)
            except Exception as e:
                print(f"Failed fallback competitor analysis: {e}")

    # --- AGENT 4: Strategy Agent ---
    print("Running Agent 4: Strategy Agent...")
    agent4_prompt = (
        "You are a startup strategist. Compare this idea against these competitors. "
        "Return ONLY valid JSON with keys:\n"
        "- feature_matrix: object where each key is a feature name (8 features total), value is object with keys: user_idea (boolean), and one key per competitor name (boolean)\n"
        "- idea_scores: object with keys innovation, market_gap, feasibility, competition_density — each a number 0-100\n"
        "- competitor_scores: array of objects, each with name and scores object (same 4 keys, each 0-100)\n"
        "- suggestions: array of 5 objects each with keys: title (string), description (string), priority (High/Medium/Low)\n"
        "- summary: 2 sentence executive summary string"
    )

    strategy_payload = {
        "parsed_idea": parsed_idea,
        "competitors": competitors
    }
    
    strategy = call_grok_json(agent4_prompt, json.dumps(strategy_payload))
    print("Generated Strategy Strategy Summary:", strategy.get("summary"))

    # Return final response
    return {
        "parsed_idea": parsed_idea,
        "competitors": competitors,
        "strategy": strategy
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

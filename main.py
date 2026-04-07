from fastapi import FastAPI
import requests
import json
from duckduckgo_search import DDGS
from bs4 import BeautifulSoup

app = FastAPI()

OLLAMA_URL = "http://localhost:11434/api/generate"


def call_ollama(prompt):
    response = requests.post(
        OLLAMA_URL,
        json={
            "model": "llama3",
            "prompt": prompt,
            "stream": False
        }
    )
    return response.json()["response"]


@app.get("/")
def home():
    return {"message": "IdeaLens Backend Running"}


@app.get("/test-llm")
def test_llm():
    prompt = "Explain what an AI startup idea is in simple terms"
    result = call_ollama(prompt)
    return {"response": result}

from fastapi import Body





@app.post("/parse-idea")
def parse_idea(data: dict = Body(...)):
    user_input = data.get("idea")

    prompt = f"""
You are an AI system that extracts structured information.

Convert the following idea into JSON.

IMPORTANT:
- Do NOT change the idea
- Do NOT invent anything
- Stay strictly relevant to the input

Idea:
{user_input}

Return ONLY valid JSON in this format:

{{
  "problem": "...",
  "target_users": "...",
  "solution": "...",
  "domain": "...",
  "key_features": ["...", "..."]
}}

NO explanation.
ONLY JSON.
"""

    result = call_ollama(prompt)

    try:
        parsed_json = json.loads(result)
    except:
        return {
            "error": "Invalid JSON from model",
            "raw_output": result
        }

    return {"parsed_idea": parsed_json}


#  Search function 

def search_duckduckgo(query):
    results = []

    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=5):
            results.append({
                "title": r.get("title"),
                "url": r.get("href"),
                "snippet": r.get("body")
            })

    return results


# Fetch website content

def fetch_website_content(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=5)

        soup = BeautifulSoup(response.text, "html.parser")

        paragraphs = soup.find_all("p")
        text = " ".join([p.get_text() for p in paragraphs[:5]])

        return text

    except:
        return ""
    

 # Wesite understanding agent 

def analyze_website(content):
    prompt = f"""
You are analyzing a startup website.

Extract:
- name
- what it does
- key features

Content:
{content}

Return ONLY JSON:
{{
  "name": "",
  "description": "",
  "features": ["", ""]
}}
"""

    result = call_ollama(prompt)

    try:
        return json.loads(result)
    except:
        return {"error": "Invalid response", "raw": result}
    
## Main End Points

@app.post("/analyze-idea")
def analyze_idea(data: dict = Body(...)):
    idea = data.get("idea")

    # Step 1: Parse idea
    parsed = parse_idea({"idea": idea})["parsed_idea"]

    # Step 2: Build search query
    query = f"{parsed['solution']} {parsed['domain']} startup"

    # Step 3: Search
    results = search_duckduckgo(query)

    analyzed_results = []

    for r in results[:3]:
        content = fetch_website_content(r["url"])

        if content:
            analysis = analyze_website(content)

            analyzed_results.append({
                "url": r["url"],
                "analysis": analysis
            })

    return {
        "parsed_idea": parsed,
        "similar_products": analyzed_results
    }
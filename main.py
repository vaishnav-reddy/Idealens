from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

def get_grok_client():
    api_key = os.getenv("GROK_API_KEY")

    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="GROK_API_KEY environment variable is not set."
        )

    return OpenAI(
        api_key=api_key,
        base_url="https://api.groq.com/openai/v1"
    )

# ---------------- BASIC ROUTES ----------------
@app.get("/")
def home():
    return {"message": "IdeaLens Backend Running"}


@app.get("/test-llm")
def test_llm():
    prompt = "Explain what an AI startup idea is in simple terms"
    result = call_ollama(prompt)
    return {"response": result}


# ---------------- IDEA PARSER ----------------
def parse_idea_internal(user_input):
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
        return json.loads(result)
    except:
        return {"error": "Invalid JSON", "raw": result}


@app.post("/parse-idea")
def parse_idea(data: dict = Body(...)):
    return {"parsed_idea": parse_idea_internal(data.get("idea"))}


# ---------------- SEARCH ----------------
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


# ---------------- FETCH WEBSITE ----------------
def fetch_website_content(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)

        soup = BeautifulSoup(response.text, "html.parser")

        text = soup.get_text(separator=" ", strip=True)

        return text[:2000]

    except Exception as e:
        print("Error fetching:", url, e)
        return ""


# ---------------- ANALYZE WEBSITE ----------------
def extract_json(text):
    try:
        # Find JSON inside text using regex
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
        else:
            return {"error": "No JSON found", "raw": text}
    except:
        return {"error": "Invalid JSON", "raw": text}


def analyze_website(content):
    prompt = f"""
You are analyzing a startup website.

Extract:
- name
- what it does
- key features

IMPORTANT:
Return ONLY JSON.
No explanation.

Content:
{content}

Format:
{{
  "name": "",
  "description": "",
  "features": ["", ""]
}}
"""

    result = call_ollama(prompt)

    return extract_json(result)
    print("QUERY:", query)
    print("SEARCH RESULTS:", results)


# ---------------- MAIN PIPELINE ----------------
@app.post("/analyze-idea")
def analyze_idea(data: dict = Body(...)):
    idea = data.get("idea")

    # Step 1: Parse idea
    parsed = parse_idea_internal(idea)

    # Step 2: Search query
    query = f"{parsed['solution']} {parsed['domain']} startup OR app OR platform"

    # Step 3: Search
    results = search_duckduckgo(query)

    analyzed_results = []

    # Step 4: Process each result
    for r in results[:3]:
        print("URL:", r["url"])

        content = fetch_website_content(r["url"])
        print("Content length:", len(content))

        if content:
            analysis = analyze_website(content)

            analyzed_results.append({
                "url": r["url"],
                "analysis": analysis
            })

    # Step 5: Fallback
    if not analyzed_results:
        return {
            "parsed_idea": parsed,
            "message": "No detailed analysis found",
            "search_results": results
        }

    return {
        "parsed_idea": parsed,
        "similar_products": analyzed_results
    }
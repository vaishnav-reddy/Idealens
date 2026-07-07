# IdeaLens

**AI-powered startup idea validation and competitive intelligence platform.**

IdeaLens takes a startup idea as input and runs it through a 4-agent pipeline that searches the live web, discovers real competitors, extracts grounded product intelligence, and generates an interactive strategy dashboard — all in real time.

---

## What It Does

Enter any startup idea in plain text. IdeaLens:

1. **Parses** your idea into structured business signals
2. **Discovers** real competing products via live web search
3. **Verifies** each result is an actual product homepage (not a blog, listicle, or review aggregator)
4. **Extracts** grounded competitor profiles from scraped content
5. **Generates** a visual strategy dashboard with:
   - Competitor cards with real URLs and sourced feature lists
   - Radar chart comparing your idea against top competitors
   - Feature gap matrix highlighting differentiation opportunities
   - Idea scoring across Innovation, Market Gap, Feasibility, and Competition Density
   - Prioritized differentiation recommendations

---

## Agent Pipeline Architecture

```
User Idea Input
      ↓
[Agent 1] Idea Parser
  → Extracts: problem, solution, domain, target users,
    key features, unique angle, likely competitors,
    industry search terms
      ↓
[Agent 2] Search & Discovery
  → Tier 1: Verifies named competitors directly
  → Tier 2: Runs targeted search queries via Tavily
  → Listicle Mining: Extracts product names from
    comparison articles, verifies each one
  → Domain blocklist filters noise (Wikipedia, YouTube,
    Reddit, LinkedIn, etc.)
      ↓
[Agent 3] Competitor Intelligence
  → Fetches each verified product page via Jina Reader
  → Extracts grounded profiles: features, pricing,
    strengths, weaknesses — all sourced from page text
  → "Not stated on website" instead of invented values
  → Evidence snippets stored for source verification
      ↓
[Agent 4] Strategy Agent
  → Feature matrix comparing idea vs all competitors
  → Idea scores (0–100) across 4 dimensions
  → 5 prioritized differentiation recommendations
  → Executive summary grounded in verified competitor data
      ↓
Visual Dashboard
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI (Python) |
| Frontend | Next.js + Tailwind CSS |
| LLM | Groq (Llama 3.3 70B) |
| Web Search | Tavily API |
| Web Extraction | Jina Reader API |
| Charts | Recharts |
| Icons | Lucide React |

---

## Getting Started

### Prerequisites

- Python 3.10+
- Node.js 18+
- API keys for Groq, Tavily, and Jina Reader (all free tier)

### Backend Setup

```bash
cd backend
python -m venv venv

# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
# Add your API keys to .env
python main.py
```

Backend runs at `http://localhost:8000`

### Frontend Setup

```bash
cd frontend
npm install
# Add NEXT_PUBLIC_API_URL=http://localhost:8000 to .env.local
npm run dev
```

Frontend runs at `http://localhost:3000`

---

## API Keys

| Key | Where to Get | Cost |
|---|---|---|
| `GROQ_API_KEY` | [console.groq.com](https://console.groq.com) | Free tier |
| `TAVILY_API_KEY` | [app.tavily.com](https://app.tavily.com) | Free tier (1000 searches/mo) |
| `JINA_API_KEY` | [jina.ai/reader](https://jina.ai/reader) | Free tier |

---

## Project Structure

```
idealens/
├── backend/
│   ├── main.py              # FastAPI app + 4-agent pipeline
│   ├── requirements.txt     # Python dependencies
│   ├── .env.example         # Environment variable template
│   └── test_regression.py   # Pipeline regression tests
├── frontend/
│   ├── pages/
│   │   ├── index.js         # Main dashboard UI
│   │   ├── _app.js          # Next.js app wrapper
│   │   └── _document.js     # Next.js document config
│   ├── styles/
│   │   └── globals.css      # Global styles
│   ├── public/
│   │   └── favicon.ico
│   └── package.json
└── README.md
```

---

## Key Design Decisions

**Zero hallucination standard** — every fact shown in a competitor card is extracted from scraped page content. Fields that cannot be sourced from the page display "Not stated on website" rather than an invented value. Each card includes a "Verify sources" section showing the exact text snippet used for each extracted field.

**Source classification before extraction** — the pipeline classifies every URL as a product homepage, listicle, or irrelevant page before running expensive extraction. Listicles are mined for product names and used as discovery signals rather than competitor profiles.

**Domain blocklist** — Wikipedia, YouTube, Reddit, LinkedIn, app stores, and review aggregators are skipped before any fetch or LLM call, preserving API quota for real product pages.

**Rate limit resilience** — Jina Reader requests are limited to 2 concurrent with 500ms stagger. Groq calls use a 3-slot semaphore with exponential backoff on 429 responses.

---

## Limitations

- Results depend on Groq's free-tier daily token quota. Heavy usage (5+ analyses/day) may trigger rate limiting.
- Some product websites block web scrapers (403/401 responses). Affected competitors are skipped and logged in Pipeline Diagnostics.
- The pipeline targets English-language products and queries.

---

## Built By

**Appala Vaishnav Reddy**  
B.Tech Computer Science, Parul Institute of Engineering and Technology  
[github.com/vaishnav-reddy](https://github.com/vaishnav-reddy)

# IdeaLens — AI-Powered Idea Validation & Competitive Intelligence Platform

IdeaLens is a full-stack AI web application that helps creators, startup founders, and developers validate their business concepts before writing code. A user inputs their startup idea, and an autonomous 4-Agent pipeline performs structured extraction, web search discovery, product copies analysis, and strategic positioning to produce a highly visual, dark-themed competitive dashboard.

## System Architecture

```
                       +---------------------------------------+
                       |              User Input               |
                       +---------------------------------------+
                                           |
                                           v
                       +---------------------------------------+
                       |        AGENT 1: Idea Parser           |
                       | (Extracts problem, solution, domain)  |
                       +---------------------------------------+
                                           |
                                           v
                       +---------------------------------------+
                       |      AGENT 2: Search & Discovery      |
                       |   (Finds competitors via Tavily API)  |
                       +---------------------------------------+
                                           |
                                           v
                       +---------------------------------------+
                       |    AGENT 3: Competitor Intelligence    |
                       |   (Parses copy via Jina & Grok API)   |
                       +---------------------------------------+
                                           |
                                           v
                       +---------------------------------------+
                       |        AGENT 4: Strategy Agent        |
                       |  (Generates matrices, scores, advice) |
                       +---------------------------------------+
                                           |
                                           v
                       +---------------------------------------+
                       |          Frontend Dashboard           |
                       |  (Interactive Radar, matrix, scores)  |
                       +---------------------------------------+
```

---

## Folder Structure

```
/idealens
  ├── /backend
  │    ├── main.py              # FastAPI server with 4-agent workflow
  │    ├── requirements.txt     # Python package dependencies
  │    └── .env.example         # Example backend environment configuration
  │
  ├── /frontend
  │    ├── /pages
  │    │    └── index.js        # Main React dashboard layout and UI logic
  │    ├── /styles
  │    │    └── globals.css     # Dark theme CSS variable stylesheet
  │    ├── package.json         # Node.js configurations and dependencies
  │    └── .env.local           # Local frontend environment file
  │
  └── README.md                 # Project guide
```

---

## Prerequisites

- **Python**: v3.9 or higher
- **Node.js**: v18.0.0 or higher & **npm**

---

## Installation & Local Setup

### 1. Backend Setup (FastAPI)

1. Open your terminal and navigate to the backend directory:
   ```bash
   cd backend
   ```
2. Create a Python virtual environment and activate it:
   ```bash
   python -m venv venv
   # On Windows:
   .\venv\Scripts\activate
   # On macOS/Linux:
   source venv/bin/activate
   ```
3. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Copy the environment template and fill in your API keys:
   ```bash
   copy .env.example .env
   # or
   cp .env.example .env
   ```
5. Edit `.env` to include your actual API keys:
   ```env
   GROK_API_KEY=your_xai_key_here
   TAVILY_API_KEY=your_tavily_key_here
   ```
6. Start the FastAPI development server:
   ```bash
   python main.py
   ```
   The backend server will run on `http://localhost:8000`.

### 2. Frontend Setup (Next.js)

1. In a new terminal window, navigate to the frontend directory:
   ```bash
   cd frontend
   ```
2. Install the frontend package dependencies:
   ```bash
   npm install
   ```
3. The `.env.local` file is already created, pointing to the local API:
   ```env
   NEXT_PUBLIC_API_URL=http://localhost:8000
   ```
4. Run the development server:
   ```bash
   npm run dev
   ```
   Open your browser and navigate to `http://localhost:3000` to see the live application.

---

## Deployment Guide

### Deploying the Backend (Render)

1. Sign up/Log in to [Render](https://render.com/).
2. Create a new **Web Service** and connect it to your GitHub repository containing the backend code.
3. Configure the service settings:
   - **Environment**: `Python`
   - **Root Directory**: `backend`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Add the following **Environment Variables** in the Render settings:
   - `GROK_API_KEY`: *(your secret xAI key)*
   - `TAVILY_API_KEY`: *(your secret Tavily key)*
5. Click **Deploy Web Service**. Render will build and deploy the backend container. Keep track of the generated live URL (e.g. `https://idealens-backend.onrender.com`).

### Deploying the Frontend (Vercel)

1. Sign up/Log in to [Vercel](https://vercel.com/).
2. Create a new project and import your repository.
3. Configure the build parameters:
   - **Framework Preset**: `Next.js`
   - **Root Directory**: `frontend`
4. Add the following **Environment Variables** in Vercel settings:
   - `NEXT_PUBLIC_API_URL`: `https://your-backend-render-url.onrender.com` (use your deployed Render backend URL)
5. Click **Deploy**. Vercel will build, optimize, and serve your Next.js application globally.

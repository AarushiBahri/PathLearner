from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict
from uuid import uuid4
import os
import json
import re
from dotenv import load_dotenv
from openai import OpenAI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse


# -----------------------------
# Load environment variables
# -----------------------------

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MODEL = os.getenv("OPENAI_MODEL")

# -----------------------------
# FastAPI App
# -----------------------------
app = FastAPI()
app.mount("/Public", StaticFiles(directory="Public"), name="Public")

from fastapi.responses import HTMLResponse

@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html>
        <head>
    <meta charset="UTF-8" />
    <link rel="icon" type="image/png" href="/Public/PathLearner.png" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>PathLearner Backend</title>
            <style>
                body {
                    font-family: Inter, sans-serif;
                    margin: 0;
                    padding: 0;
                    background: linear-gradient(135deg, #eef2ff, #fdf2f8);
                    height: 100vh;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                }
                .card {
                    background: white;
                    padding: 40px;
                    border-radius: 16px;
                    text-align: center;
                    max-width: 460px;
                    box-shadow: 0px 12px 35px rgba(0,0,0,0.08);
                    animation: fadeIn 0.7s ease-out;
                }
                h1 {
                    font-size: 32px;
                    margin-bottom: 12px;
                    color: #1f2937;
                }
                p {
                    font-size: 16px;
                    color: #4b5563;
                }
                .status {
                    margin-top: 20px;
                    font-size: 14px;
                    padding: 10px 16px;
                    display: inline-block;
                    background: #ecfdf5;
                    color: #065f46;
                    border-radius: 8px;
                    border: 1px solid #a7f3d0;
                }
                @keyframes fadeIn {
                    from { opacity: 0; transform: translateY(20px); }
                    to { opacity: 1; transform: translateY(0); }
                }
            </style>
        </head>
        <body>
            <div class="card">
                <h1>PathLearner Backend</h1>
                <p>Your AI-powered roadmap server is up and running.</p>
                <div class="status">🟢 Status: Active & Ready</div>
            </div>
        </body>
    </html>
    """



app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Dev → allow all
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# Request Models
# -----------------------------
class GenerateRequest(BaseModel):
    goal: str
    time_per_week_hours: int
    background: str

class MaterialsRequest(BaseModel):
    topic: str

class SummaryRequest(BaseModel):
    topic: str

class ConfusionRequest(BaseModel):
    goal: str
    roadmap: List[Dict]
    current_topic: str
    confusion_text: str


# -----------------------------
# Prompt Builders
# -----------------------------
def generate_roadmap_prompt(goal, hours, background):
    return f"""
You are an expert curriculum designer.

Create a learning roadmap for: "{goal}".
Background: "{background}".
Available time: {hours} hours/week.

Return ONLY valid JSON (no markdown, no explanation).
Format:
[
  {{
    "id": "unique_slug",
    "title": "Topic Name",
    "description": "1 sentence summary",
    "estimated_hours": 5
  }}
]

Create 8–12 topics.
"""

def materials_prompt(topic):
    return f"""
Give 5 high-quality learning resources for "{topic}".

Return ONLY JSON:
[
  {{
    "title": "resource name",
    "url": "https://...",
    "type": "video/article/documentation"
  }}
]
"""

def summary_prompt(topic):
    return f"""
Explain "{topic}" in 2–3 simple beginner-friendly sentences.

Return ONLY valid JSON like:

{{
  "title": "{topic}",
  "summary": "..."
}}
"""

def confusion_prompt(goal, roadmap, topic, confusion):
    titles = [r["title"] for r in roadmap]
    return f"""
The learner's goal: "{goal}".

Their roadmap topics: {titles}

They are confused about: "{topic}"
Confusion: "{confusion}"

Identify up to 3 **missing prerequisite topics** the user must learn BEFORE "{topic}".

Return ONLY JSON:
[
  {{
    "id": "unique_slug",
    "title": "Missing prerequisite",
    "description": "1 sentence why it matters",
    "estimated_hours": 3
  }}
]
"""


# -----------------------------
# ROUTES
# -----------------------------

@app.post("/generate_roadmap")
async def generate_roadmap(req: GenerateRequest):
    prompt = generate_roadmap_prompt(req.goal, req.time_per_week_hours, req.background)

    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=900,
        temperature=0.2,
    )

    text = response.choices[0].message.content

    # SAFE JSON EXTRACTION
    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        return {"error": "Could not find JSON", "raw": text}

    data = json.loads(match.group(0))
    return {"roadmap": data}


@app.post("/get_materials")
async def get_materials(req: MaterialsRequest):
    prompt = materials_prompt(req.topic)

    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=600,
       temperature=0.3,
    )

    text = response.choices[0].message.content

    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        return {"resources": [], "raw": text}

    data = json.loads(match.group(0))
    return {"resources": data}


@app.post("/get_summary")
async def get_summary(req: SummaryRequest):
    prompt = summary_prompt(req.topic)

    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=300,
        temperature=0.2
    )

    text = response.choices[0].message.content

    # Extract JSON safely
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return {
            "title": req.topic,
            "summary": "No summary generated.",
            "raw": text
        }

    try:
        data = json.loads(match.group(0))
    except:
        return {
            "title": req.topic,
            "summary": "No summary generated.",
            "raw": text
        }

    return data


@app.post("/handle_confusion")
async def handle_confusion(req: ConfusionRequest):
    prompt = confusion_prompt(
        req.goal,
        req.roadmap,
        req.current_topic,
        req.confusion_text
    )

    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=700,
        temperature=0.1
    )

    text = response.choices[0].message.content

    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        return {"new_prereqs": [], "raw": text}

    data = json.loads(match.group(0))

    # sanitize fields
    cleaned = []
    for item in data:
        cleaned.append({
            "id": item.get("id") or f"pre-{uuid4()}",
            "title": item.get("title") or "Missing prerequisite",
            "description": item.get("description", ""),
            "estimated_hours": item.get("estimated_hours", 3),
        })

    return {"new_prereqs": cleaned}

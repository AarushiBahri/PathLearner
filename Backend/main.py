import asyncio
from datetime import datetime
import math
from typing import Dict, List, Optional
from uuid import uuid4
import json
import os
import re
from urllib.parse import parse_qs, unquote, urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship


load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL")
OPENAI_EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
DATABASE_URL = os.getenv("DATABASE_URL")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
TAVILY_PROJECT_ID = os.getenv("TAVILY_PROJECT_ID")
SEARCH_TIMEOUT_SECONDS = 12
MAX_WEB_RESULTS = 120
SHORTLIST_SIZE = 40
FINAL_RESOURCE_COUNT = 24
MAX_QUERY_COUNT = 24
MIN_CANDIDATES_FOR_EARLY_STOP = 28


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    roadmaps: Mapped[List["Roadmap"]] = relationship(back_populates="user")


class Roadmap(Base):
    __tablename__ = "roadmaps"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    goal: Mapped[str] = mapped_column(String(255))
    background: Mapped[str] = mapped_column(Text, default="")
    time_per_week_hours: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    user: Mapped[User] = relationship(back_populates="roadmaps")
    items: Mapped[List["RoadmapItem"]] = relationship(
        back_populates="roadmap",
        cascade="all, delete-orphan",
        order_by="RoadmapItem.position",
    )


class RoadmapItem(Base):
    __tablename__ = "roadmap_items"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    roadmap_id: Mapped[str] = mapped_column(ForeignKey("roadmaps.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    estimated_hours: Mapped[int] = mapped_column(Integer, default=3)
    progress: Mapped[str] = mapped_column(String(32), default="not-started")
    roadmap: Mapped[Roadmap] = relationship(back_populates="items")


class Resource(Base):
    __tablename__ = "resources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    resource_type: Mapped[str] = mapped_column(String(32))
    difficulty: Mapped[str] = mapped_column(String(32), default="beginner")
    is_free: Mapped[str] = mapped_column(String(8), default="true")
    rating: Mapped[int] = mapped_column(Integer, default=4)
    topics_json: Mapped[str] = mapped_column(Text, default="[]")
    embedding_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class QuizSession(Base):
    __tablename__ = "quiz_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    client_id: Mapped[str] = mapped_column(String(128), index=True)
    roadmap_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    roadmap_item_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    quiz_type: Mapped[str] = mapped_column(String(32), index=True)
    topic: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255))
    questions_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(ForeignKey("quiz_sessions.id"), index=True)
    client_id: Mapped[str] = mapped_column(String(128), index=True)
    roadmap_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    roadmap_item_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    quiz_type: Mapped[str] = mapped_column(String(32), index=True)
    topic: Mapped[str] = mapped_column(String(255))
    score_percent: Mapped[int] = mapped_column(Integer)
    confidence_score: Mapped[int] = mapped_column(Integer)
    answers_json: Mapped[str] = mapped_column(Text, default="[]")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


engine = None
if DATABASE_URL:
    engine = create_engine(DATABASE_URL, future=True)
    Base.metadata.create_all(engine)


app = FastAPI()
app.mount("/Public", StaticFiles(directory="Public"), name="Public")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class EnsureUserRequest(BaseModel):
    client_id: str


class GenerateRequest(BaseModel):
    client_id: str
    goal: str
    time_per_week_hours: int
    background: str
    diagnostic_result: Optional[Dict] = None


class MaterialsRequest(BaseModel):
    topic: str
    progress: Optional[str] = None
    seen_titles: Optional[List[str]] = None
    pricing: Optional[str] = None
    resource_type: Optional[str] = None
    difficulty: Optional[str] = None


class SummaryRequest(BaseModel):
    topic: str


class ConfusionRequest(BaseModel):
    client_id: str
    goal: str
    roadmap: List[Dict]
    current_topic: str
    confusion_text: str


class ProgressUpdateRequest(BaseModel):
    progress: str


class DiagnosticGenerateRequest(BaseModel):
    client_id: str
    goal: str
    background: str


class TopicQuizGenerateRequest(BaseModel):
    client_id: str
    topic: str
    roadmap_item_id: Optional[str] = None
    goal: Optional[str] = None
    background: Optional[str] = None
    level: Optional[str] = None


class QuizSubmitRequest(BaseModel):
    client_id: str
    session_id: str
    answers: List[int]


TRUSTED_RESOURCE_DOMAINS = [
    "coursera.org",
    "edx.org",
    "khanacademy.org",
    "ocw.mit.edu",
    "openlearninglibrary.mit.edu",
    "classcentral.com",
    "futurelearn.com",
    "udacity.com",
    "canvas.net",
    "open.edu",
    "alison.com",
    "saylor.org",
    "sololearn.com",
    "codecademy.com",
    "freecodecamp.org",
    "developer.mozilla.org",
    "w3schools.com",
    "geeksforgeeks.org",
    "docs.python.org",
    "realpython.com",
    "roadmap.sh",
    "javascript.info",
    "fullstackopen.com",
    "postgresql.org",
    "fastapi.tiangolo.com",
    "sqlalchemy.org",
    "scikit-learn.org",
    "huggingface.co",
    "platform.openai.com",
    "cloud.google.com",
    "aws.amazon.com",
    "learn.microsoft.com",
    "oracle.com",
    "ibm.com",
    "developers.google.com",
    "developer.android.com",
    "react.dev",
    "vuejs.org",
    "angular.dev",
    "rust-lang.org",
    "go.dev",
    "nodejs.org",
    "mongodb.com",
    "redis.io",
    "docker.com",
    "kubernetes.io",
    "terraform.io",
    "linuxfoundation.org",
    "cisco.com",
    "owasp.org",
    "nist.gov",
    "cs50.harvard.edu",
    "ocw.metu.edu.tr",
    "youtube.com",
    "youtu.be",
    "github.com",
]


def require_database():
    if engine is None:
        raise HTTPException(
            status_code=503,
            detail="Missing DATABASE_URL in backend environment."
        )
    return engine


def require_openai_config():
    if not OPENAI_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="Missing OPENAI_API_KEY in backend environment."
        )

    if not OPENAI_MODEL:
        raise HTTPException(
            status_code=503,
            detail="Missing OPENAI_MODEL in backend environment."
        )

    return OpenAI(api_key=OPENAI_API_KEY)


def generate_text(prompt: str, max_tokens: int, temperature: float) -> str:
    client = require_openai_config()

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"OpenAI request failed: {exc}"
        ) from exc

    text = response.choices[0].message.content
    if not text:
        raise HTTPException(status_code=502, detail="OpenAI returned empty text.")

    return text


CURATED_RESOURCES = [
    {
        "slug": "aws-devops-docs",
        "title": "AWS Documentation: DevOps",
        "url": "https://docs.aws.amazon.com/",
        "description": "Official AWS documentation for CI/CD, infrastructure as code, deployment automation, monitoring, and operations workflows.",
        "resource_type": "documentation",
        "difficulty": "intermediate",
        "is_free": "true",
        "rating": 5,
        "topics": ["aws devops", "ci cd", "infrastructure as code", "automation"],
    },
    {
        "slug": "aws-devops-essentials",
        "title": "AWS DevOps Essentials",
        "url": "https://explore.skillbuilder.aws/learn",
        "description": "Hands-on video learning path for AWS DevOps fundamentals including deployment pipelines, CloudFormation, and operational automation.",
        "resource_type": "video",
        "difficulty": "beginner",
        "is_free": "true",
        "rating": 4,
        "topics": ["aws devops", "cloudformation", "pipelines", "deployment"],
    },
    {
        "slug": "aws-devops-beginners",
        "title": "AWS DevOps for Beginners",
        "url": "https://aws.amazon.com/devops/what-is-devops/",
        "description": "Introductory overview of DevOps concepts on AWS, including CI/CD, automation, infrastructure, and monitoring at a beginner level.",
        "resource_type": "article",
        "difficulty": "beginner",
        "is_free": "true",
        "rating": 4,
        "topics": ["aws devops", "devops basics", "ci cd", "cloud automation"],
    },
    {
        "slug": "aws-cloudformation-intro",
        "title": "AWS CloudFormation Basics",
        "url": "https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/Welcome.html",
        "description": "Beginner-friendly starting point for infrastructure as code on AWS using CloudFormation templates and stacks.",
        "resource_type": "documentation",
        "difficulty": "beginner",
        "is_free": "true",
        "rating": 4,
        "topics": ["cloudformation", "infrastructure as code", "aws templates", "aws devops"],
    },
    {
        "slug": "aws-cicd-workshop",
        "title": "AWS CI/CD Workshop",
        "url": "https://catalog.workshops.aws/cicdonaws/en-US",
        "description": "Hands-on guided workshop for building continuous integration and deployment pipelines on AWS.",
        "resource_type": "course",
        "difficulty": "intermediate",
        "is_free": "true",
        "rating": 5,
        "topics": ["ci cd", "aws pipelines", "deployment automation", "aws devops"],
    },
    {
        "slug": "huggingface-transformers-course",
        "title": "Hugging Face Transformers Course",
        "url": "https://huggingface.co/learn/nlp-course",
        "description": "Project-based course covering transformers, fine-tuning, tokenization, model training, evaluation, and deployment.",
        "resource_type": "course",
        "difficulty": "intermediate",
        "is_free": "true",
        "rating": 5,
        "topics": ["fine tuning llms", "transformers", "nlp", "model training"],
    },
    {
        "slug": "llm-intro-visual-guide",
        "title": "Introduction to Large Language Models",
        "url": "https://www.youtube.com/watch?v=zjkBMFhNj_g",
        "description": "Beginner-friendly introduction to what large language models are, how they work, and where fine-tuning fits in.",
        "resource_type": "video",
        "difficulty": "beginner",
        "is_free": "true",
        "rating": 5,
        "topics": ["llms", "large language models", "generative ai", "fine tuning llms"],
    },
    {
        "slug": "fine-tuning-basics-guide",
        "title": "Fine-Tuning Basics for Beginners",
        "url": "https://huggingface.co/learn/llm-course/chapter11/3",
        "description": "A beginner-oriented explanation of fine-tuning concepts, datasets, prompts, and evaluation before moving into advanced optimization.",
        "resource_type": "article",
        "difficulty": "beginner",
        "is_free": "true",
        "rating": 4,
        "topics": ["fine tuning llms", "instruction tuning", "datasets", "llm basics"],
    },
    {
        "slug": "attention-transformers-intro",
        "title": "Transformers Explained Simply",
        "url": "https://jalammar.github.io/illustrated-transformer/",
        "description": "Visual explanation of transformers, attention, encoders, decoders, and why these architectures matter for modern language models.",
        "resource_type": "article",
        "difficulty": "beginner",
        "is_free": "true",
        "rating": 5,
        "topics": ["transformers", "attention", "llm basics", "nlp fundamentals"],
    },
    {
        "slug": "prompting-and-instruction-tuning",
        "title": "Instruction Tuning and Prompting Concepts",
        "url": "https://huggingface.co/blog/instruction-tuning-sd",
        "description": "Intermediate guide explaining instruction tuning, prompting behavior, and task adaptation for language models.",
        "resource_type": "article",
        "difficulty": "intermediate",
        "is_free": "true",
        "rating": 4,
        "topics": ["instruction tuning", "prompting", "fine tuning llms", "task adaptation"],
    },
    {
        "slug": "openai-fine-tuning-guide",
        "title": "OpenAI Fine-Tuning Guide",
        "url": "https://platform.openai.com/docs/guides/fine-tuning",
        "description": "Official guide to fine-tuning models, preparing datasets, evaluating results, and choosing the right training strategy.",
        "resource_type": "documentation",
        "difficulty": "intermediate",
        "is_free": "true",
        "rating": 5,
        "topics": ["fine tuning llms", "openai", "evaluation", "datasets"],
    },
    {
        "slug": "fastapi-tutorial",
        "title": "FastAPI Tutorial",
        "url": "https://fastapi.tiangolo.com/tutorial/",
        "description": "Official tutorial for building Python APIs with request validation, routing, async handlers, and database integration.",
        "resource_type": "documentation",
        "difficulty": "beginner",
        "is_free": "true",
        "rating": 5,
        "topics": ["fastapi", "python backend", "api development", "sqlalchemy"],
    },
    {
        "slug": "fastapi-path-params",
        "title": "FastAPI Path and Query Parameters",
        "url": "https://fastapi.tiangolo.com/tutorial/query-params/",
        "description": "Focused tutorial on routing, path params, query params, and request handling in FastAPI.",
        "resource_type": "documentation",
        "difficulty": "beginner",
        "is_free": "true",
        "rating": 4,
        "topics": ["fastapi routing", "query params", "path params", "api development"],
    },
    {
        "slug": "sqlalchemy-orm-quickstart",
        "title": "SQLAlchemy ORM Quickstart",
        "url": "https://docs.sqlalchemy.org/en/20/orm/quickstart.html",
        "description": "Official SQLAlchemy quickstart covering models, sessions, inserts, queries, and relational mappings.",
        "resource_type": "documentation",
        "difficulty": "intermediate",
        "is_free": "true",
        "rating": 5,
        "topics": ["sqlalchemy", "orm", "database integration", "python backend"],
    },
    {
        "slug": "sqlbolt-sql-intro",
        "title": "SQLBolt Interactive SQL Lessons",
        "url": "https://sqlbolt.com/",
        "description": "Interactive beginner-friendly SQL lessons covering selects, joins, updates, and relational database basics.",
        "resource_type": "article",
        "difficulty": "beginner",
        "is_free": "true",
        "rating": 5,
        "topics": ["sql", "postgresql", "database basics", "joins"],
    },
    {
        "slug": "postgresql-official-tutorial",
        "title": "PostgreSQL Official Tutorial",
        "url": "https://www.postgresql.org/docs/current/tutorial.html",
        "description": "Core PostgreSQL tutorial for creating tables, inserting data, querying records, and managing relational data.",
        "resource_type": "documentation",
        "difficulty": "beginner",
        "is_free": "true",
        "rating": 5,
        "topics": ["postgresql", "relational data", "sql", "database setup"],
    },
    {
        "slug": "postgres-joins-explained",
        "title": "SQL Joins Explained Visually",
        "url": "https://joins.spathon.com/",
        "description": "Visual walkthrough of SQL join types for learners building confidence with relational queries.",
        "resource_type": "article",
        "difficulty": "beginner",
        "is_free": "true",
        "rating": 4,
        "topics": ["sql joins", "postgresql", "relational queries", "database basics"],
    },
    {
        "slug": "statquest-cross-validation",
        "title": "StatQuest: Cross Validation",
        "url": "https://www.youtube.com/watch?v=fSytzGwwBVw",
        "description": "Beginner-friendly explanation of cross-validation, train-test splits, and model evaluation concepts.",
        "resource_type": "video",
        "difficulty": "beginner",
        "is_free": "true",
        "rating": 5,
        "topics": ["model evaluation", "cross validation", "machine learning metrics"],
    },
    {
        "slug": "scikit-learn-model-evaluation",
        "title": "scikit-learn Model Evaluation Guide",
        "url": "https://scikit-learn.org/stable/modules/model_evaluation.html",
        "description": "Reference guide for model metrics, validation techniques, and evaluation workflows in machine learning.",
        "resource_type": "documentation",
        "difficulty": "intermediate",
        "is_free": "true",
        "rating": 5,
        "topics": ["model evaluation", "machine learning", "metrics", "validation"],
    },
    {
        "slug": "google-ml-crash-course-intro",
        "title": "Machine Learning Crash Course",
        "url": "https://developers.google.com/machine-learning/crash-course",
        "description": "A beginner-friendly course covering ML basics, supervised learning, loss, metrics, and evaluation.",
        "resource_type": "course",
        "difficulty": "beginner",
        "is_free": "true",
        "rating": 5,
        "topics": ["machine learning basics", "supervised learning", "model evaluation", "feature engineering"],
    },
    {
        "slug": "feature-engineering-guide",
        "title": "Feature Engineering for Machine Learning",
        "url": "https://developers.google.com/machine-learning/data-prep/transform/feature-engineering",
        "description": "Practical guide to feature transformations, encoding, scaling, and feature selection in machine learning pipelines.",
        "resource_type": "documentation",
        "difficulty": "intermediate",
        "is_free": "true",
        "rating": 4,
        "topics": ["feature engineering", "data preprocessing", "encoding", "machine learning"],
    },
    {
        "slug": "supervised-learning-intro",
        "title": "Supervised Learning Introduction",
        "url": "https://www.ibm.com/think/topics/supervised-learning",
        "description": "Readable overview of supervised learning, labels, training workflows, and common use cases.",
        "resource_type": "article",
        "difficulty": "beginner",
        "is_free": "true",
        "rating": 4,
        "topics": ["supervised learning", "labels", "training data", "machine learning basics"],
    },
    {
        "slug": "unsupervised-learning-intro",
        "title": "Unsupervised Learning Explained",
        "url": "https://developers.google.com/machine-learning/clustering/overview",
        "description": "Introductory resource on clustering, similarity, dimensionality reduction, and unsupervised workflows.",
        "resource_type": "documentation",
        "difficulty": "beginner",
        "is_free": "true",
        "rating": 4,
        "topics": ["unsupervised learning", "clustering", "dimensionality reduction", "machine learning"],
    },
    {
        "slug": "java-oop-beginners",
        "title": "Object-Oriented Programming in Java for Beginners",
        "url": "https://www.baeldung.com/java-oop",
        "description": "Introductory guide to Java classes, objects, inheritance, encapsulation, and polymorphism.",
        "resource_type": "article",
        "difficulty": "beginner",
        "is_free": "true",
        "rating": 4,
        "topics": ["java oop", "classes", "inheritance", "encapsulation"],
    },
    {
        "slug": "java-syntax-basics",
        "title": "Java Syntax and Basics",
        "url": "https://www.w3schools.com/java/java_syntax.asp",
        "description": "Quick introduction to Java syntax, variables, operators, and basic program structure.",
        "resource_type": "article",
        "difficulty": "beginner",
        "is_free": "true",
        "rating": 4,
        "topics": ["java basics", "java syntax", "variables", "basic constructs"],
    },
    {
        "slug": "java-control-flow",
        "title": "Java Control Flow Statements",
        "url": "https://docs.oracle.com/javase/tutorial/java/nutsandbolts/flow.html",
        "description": "Official intro to conditionals, loops, and control structures in Java.",
        "resource_type": "documentation",
        "difficulty": "beginner",
        "is_free": "true",
        "rating": 5,
        "topics": ["java control structures", "loops", "conditionals", "java basics"],
    },
    {
        "slug": "java-methods-intro",
        "title": "Java Methods and Functions",
        "url": "https://www.w3schools.com/java/java_methods.asp",
        "description": "Simple explanation of methods, parameters, return values, and function decomposition in Java.",
        "resource_type": "article",
        "difficulty": "beginner",
        "is_free": "true",
        "rating": 4,
        "topics": ["java methods", "functions", "parameters", "java basics"],
    },
    {
        "slug": "java-exception-handling",
        "title": "Java Exception Handling Explained",
        "url": "https://docs.oracle.com/javase/tutorial/essential/exceptions/",
        "description": "Official Java tutorial on exceptions, try-catch, throws, custom exceptions, and error handling patterns.",
        "resource_type": "documentation",
        "difficulty": "beginner",
        "is_free": "true",
        "rating": 5,
        "topics": ["java exceptions", "error handling", "try catch", "java basics"],
    },
    {
        "slug": "java-collections-intro",
        "title": "Java Collections Framework Overview",
        "url": "https://docs.oracle.com/javase/tutorial/collections/intro/",
        "description": "Official overview of lists, sets, maps, and the core ideas behind the Java Collections Framework.",
        "resource_type": "documentation",
        "difficulty": "intermediate",
        "is_free": "true",
        "rating": 5,
        "topics": ["java collections", "lists", "sets", "maps", "java intermediate"],
    },
    {
        "slug": "java-file-io-intro",
        "title": "Java File I/O Basics",
        "url": "https://www.baeldung.com/java-write-to-file",
        "description": "Introduction to reading and writing files in Java using modern I/O APIs and practical examples.",
        "resource_type": "article",
        "difficulty": "intermediate",
        "is_free": "true",
        "rating": 4,
        "topics": ["java file io", "reading files", "writing files", "java intermediate"],
    },
    {
        "slug": "system-design-primer",
        "title": "System Design Primer",
        "url": "https://github.com/donnemartin/system-design-primer",
        "description": "Curated guide to scalable system design concepts, trade-offs, and architecture patterns for interviews and practical backend design.",
        "resource_type": "article",
        "difficulty": "advanced",
        "is_free": "true",
        "rating": 5,
        "topics": ["system design", "scalability", "backend architecture", "distributed systems"],
    },
]


def get_embedding(text: str) -> List[float]:
    client = require_openai_config()

    try:
        response = client.embeddings.create(
            model=OPENAI_EMBEDDING_MODEL,
            input=text,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"OpenAI embedding request failed: {exc}"
        ) from exc

    return response.data[0].embedding


def get_embeddings(texts: List[str]) -> List[List[float]]:
    if not texts:
        return []

    client = require_openai_config()

    try:
        response = client.embeddings.create(
            model=OPENAI_EMBEDDING_MODEL,
            input=texts,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"OpenAI embedding request failed: {exc}"
        ) from exc

    return [item.embedding for item in response.data]


def cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0

    dot = sum(x * y for x, y in zip(a, b))
    a_norm = math.sqrt(sum(x * x for x in a))
    b_norm = math.sqrt(sum(y * y for y in b))
    if a_norm == 0 or b_norm == 0:
        return 0.0
    return dot / (a_norm * b_norm)


def resource_embedding_text(resource: Dict) -> str:
    topics = ", ".join(resource.get("topics", []))
    return (
        f"title: {resource['title']}\n"
        f"description: {resource['description']}\n"
        f"topics: {topics}\n"
        f"type: {resource['resource_type']}\n"
        f"difficulty: {resource['difficulty']}"
    )


def normalize_domain(url: str) -> str:
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def is_trusted_domain(url: str) -> bool:
    domain = normalize_domain(url)
    return any(
        domain == trusted or domain.endswith(f".{trusted}")
        for trusted in TRUSTED_RESOURCE_DOMAINS
    )


def extract_result_url(raw_url: str) -> str:
    if "duckduckgo.com/l/?" not in raw_url:
        return raw_url

    query = urlparse(raw_url).query
    params = parse_qs(query)
    encoded = params.get("uddg", [raw_url])[0]
    return unquote(encoded)


def infer_resource_type(url: str, title: str, description: str) -> str:
    domain = normalize_domain(url)
    text = f"{title} {description} {url}".lower()

    if "youtube.com" in domain or "youtu.be" in domain:
        return "video"
    if any(
        platform in domain
        for platform in [
            "coursera.org",
            "edx.org",
            "khanacademy.org",
            "ocw.mit.edu",
            "openlearninglibrary.mit.edu",
            "classcentral.com",
            "futurelearn.com",
            "udacity.com",
            "canvas.net",
            "open.edu",
            "alison.com",
            "saylor.org",
            "sololearn.com",
            "codecademy.com",
            "cs50.harvard.edu",
        ]
    ):
        return "course"
    if "docs" in domain or "documentation" in text or "tutorial" in text:
        return "documentation"
    return "article"


def infer_difficulty(title: str, description: str) -> str:
    text = f"{title} {description}".lower()
    if any(signal in text for signal in ["advanced", "production", "optimization", "distributed"]):
        return "advanced"
    if any(signal in text for signal in ["intermediate", "deep dive", "in depth"]):
        return "intermediate"
    return "beginner"


def infer_is_free(url: str, title: str, description: str) -> bool:
    text = f"{title} {description} {url}".lower()
    if any(signal in text for signal in ["pricing", "subscription", "tuition", "paid", "premium", "enroll for", "purchase"]):
        return False
    if any(signal in text for signal in ["free", "open course", "open educational", "open source"]):
        return True
    return True


def tavily_headers() -> Dict[str, str]:
    headers = {
        "Authorization": f"Bearer {TAVILY_API_KEY}",
        "Content-Type": "application/json",
    }
    if TAVILY_PROJECT_ID:
        headers["X-Project-Id"] = TAVILY_PROJECT_ID
    return headers


def build_live_resource_candidate(topic: str, url: str, title: str, description: str) -> Dict:
    return {
        "title": title,
        "url": url,
        "description": description or f"Trusted learning resource for {topic}.",
        "type": infer_resource_type(url, title, description),
        "difficulty": infer_difficulty(title, description),
        "is_free": infer_is_free(url, title, description),
        "rating": 4,
        "topics": [topic],
        "source": "web",
        "domain": normalize_domain(url),
    }


def build_resource_queries(
    topic: str,
    pricing: Optional[str],
    resource_type: Optional[str],
    difficulty: Optional[str],
) -> List[str]:
    base_queries = [
        f"{topic} tutorial guide",
        f"{topic} examples walkthrough",
        f"{topic} fundamentals overview",
        f"{topic} deep dive",
        f"{topic} best resources",
        f"{topic} learning path",
        f"{topic} study guide",
        f"{topic} practical projects",
    ]

    type_queries = {
        "course": [
            f"{topic} free course",
            f"{topic} online course syllabus",
            f"{topic} lecture series",
            f"{topic} bootcamp",
            f"{topic} training program",
        ],
        "video": [
            f"{topic} video tutorial",
            f"{topic} lecture youtube",
            f"{topic} playlist walkthrough",
            f"{topic} recorded workshop",
        ],
        "documentation": [
            f"{topic} official documentation",
            f"{topic} docs tutorial",
            f"{topic} reference guide",
            f"{topic} developer guide",
        ],
        "article": [
            f"{topic} article tutorial",
            f"{topic} complete guide",
            f"{topic} blog tutorial",
            f"{topic} handbook",
            f"{topic} explained simply",
        ],
    }

    pricing_queries = {
        "free": [
            f"{topic} free",
            f"{topic} open course",
            f"{topic} free resource",
            f"{topic} open educational resource",
            f"{topic} free training",
        ],
        "paid": [
            f"{topic} premium course",
            f"{topic} paid training",
            f"{topic} certificate program",
            f"{topic} professional course",
        ],
    }

    difficulty_queries = {
        "beginner": [
            f"{topic} beginner",
            f"{topic} introduction",
            f"{topic} basics",
            f"{topic} for starters",
            f"{topic} foundational concepts",
        ],
        "intermediate": [
            f"{topic} intermediate",
            f"{topic} practical projects",
            f"{topic} applied tutorial",
            f"{topic} hands on",
        ],
        "advanced": [
            f"{topic} advanced",
            f"{topic} expert deep dive",
            f"{topic} production",
            f"{topic} optimization",
            f"{topic} architecture",
        ],
    }

    queries = list(base_queries)
    if resource_type and resource_type != "any":
        queries.extend(type_queries.get(resource_type, []))
        if pricing and pricing != "any":
            queries.extend(
                f"{query} {pricing}"
                for query in type_queries.get(resource_type, [])
            )
        if difficulty and difficulty != "any":
            queries.extend(
                f"{query} {difficulty}"
                for query in type_queries.get(resource_type, [])
            )
    else:
        for values in type_queries.values():
            queries.extend(values)

    if pricing and pricing != "any":
        queries.extend(pricing_queries.get(pricing, []))
        queries.extend(f"{topic} {pricing} {kind}" for kind in ["course", "video", "article", "documentation"])
    else:
        for values in pricing_queries.values():
            queries.extend(values[:2])

    if difficulty and difficulty != "any":
        queries.extend(difficulty_queries.get(difficulty, []))
        queries.extend(f"{topic} {difficulty} {kind}" for kind in ["course", "video", "article", "documentation"])
    else:
        for values in difficulty_queries.values():
            queries.extend(values[:2])

    if pricing and pricing != "any" and difficulty and difficulty != "any":
        queries.extend(
            [
                f"{topic} {pricing} {difficulty}",
                f"{topic} {pricing} {difficulty} tutorial",
                f"{topic} {pricing} {difficulty} learning resources",
            ]
        )

    if resource_type and resource_type != "any" and pricing and pricing != "any" and difficulty and difficulty != "any":
        queries.extend(
            [
                f"{topic} {pricing} {resource_type} {difficulty}",
                f"{topic} {pricing} {difficulty} {resource_type} tutorial",
                f"{topic} best {pricing} {difficulty} {resource_type} for {topic}",
            ]
        )

    seen = set()
    deduped = []
    for query in queries:
        normalized = query.strip().lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(query)
    return deduped


def limited_resource_queries(
    topic: str,
    pricing: Optional[str],
    resource_type: Optional[str],
    difficulty: Optional[str],
) -> List[str]:
    queries = build_resource_queries(topic, pricing, resource_type, difficulty)

    # Keep exact filter-intent queries first, but avoid huge sequential search fan-out.
    if resource_type and resource_type != "any" and pricing and pricing != "any" and difficulty and difficulty != "any":
        return queries[:8]
    if resource_type and resource_type != "any" and difficulty and difficulty != "any":
        return queries[:10]
    if resource_type and resource_type != "any":
        return queries[:10]
    if pricing and pricing != "any":
        return queries[:10]
    if difficulty and difficulty != "any":
        return queries[:10]
    return queries[:8]


def tavily_search_trusted_web_resources(
    topic: str,
    pricing: Optional[str],
    resource_type: Optional[str],
    difficulty: Optional[str],
) -> List[Dict]:
    queries = limited_resource_queries(topic, pricing, resource_type, difficulty)[:MAX_QUERY_COUNT]
    results_by_url: Dict[str, Dict] = {}

    for query in queries:
        payload = {
            "query": query,
            "topic": "general",
            "search_depth": "basic",
            "max_results": 8,
            "include_domains": TRUSTED_RESOURCE_DOMAINS,
            "include_answer": False,
            "include_raw_content": False,
        }

        response = requests.post(
            "https://api.tavily.com/search",
            headers=tavily_headers(),
            json=payload,
            timeout=SEARCH_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()

        for item in data.get("results", []):
            url = item.get("url", "")
            if not url or not is_trusted_domain(url) or url in results_by_url:
                continue

            title = item.get("title") or url
            description = item.get("content") or item.get("raw_content") or f"Trusted learning resource for {topic}."
            results_by_url[url] = {
                "title": title,
                "url": url,
                "description": description,
                "type": infer_resource_type(url, title, description),
                "difficulty": infer_difficulty(title, description),
                "is_free": infer_is_free(url, title, description),
                "rating": 4,
                "topics": [topic],
                "source": "web",
                "domain": normalize_domain(url),
            }

            if len(results_by_url) >= MAX_WEB_RESULTS:
                break

        if len(results_by_url) >= MAX_WEB_RESULTS or len(results_by_url) >= MIN_CANDIDATES_FOR_EARLY_STOP:
            break

    return list(results_by_url.values())


def tavily_search_query(topic: str, query: str) -> List[Dict]:
    payload = {
        "query": query,
        "topic": "general",
        "search_depth": "basic",
        "max_results": 8,
        "include_domains": TRUSTED_RESOURCE_DOMAINS,
        "include_answer": False,
        "include_raw_content": False,
    }
    response = requests.post(
        "https://api.tavily.com/search",
        headers=tavily_headers(),
        json=payload,
        timeout=SEARCH_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()

    candidates = []
    for item in data.get("results", []):
        url = item.get("url", "")
        if not url or not is_trusted_domain(url):
            continue
        title = item.get("title") or url
        description = item.get("content") or item.get("raw_content") or f"Trusted learning resource for {topic}."
        candidates.append(build_live_resource_candidate(topic, url, title, description))
    return candidates


def search_trusted_web_resources(
    topic: str,
    pricing: Optional[str],
    resource_type: Optional[str],
    difficulty: Optional[str],
) -> List[Dict]:
    queries = limited_resource_queries(topic, pricing, resource_type, difficulty)[:MAX_QUERY_COUNT]
    headers = {
        "User-Agent": "Mozilla/5.0 PathLearner Resource Retriever",
    }
    results_by_url: Dict[str, Dict] = {}

    for query in queries:
        try:
            response = requests.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers=headers,
                timeout=SEARCH_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
        except requests.RequestException:
            continue

        soup = BeautifulSoup(response.text, "html.parser")
        for anchor in soup.select("a.result__a"):
            url = extract_result_url(anchor.get("href", ""))
            if not url or not is_trusted_domain(url):
                continue

            block = anchor.find_parent(class_="result")
            snippet = ""
            if block:
                snippet_node = block.select_one(".result__snippet")
                if snippet_node:
                    snippet = snippet_node.get_text(" ", strip=True)

            if url in results_by_url:
                continue

            title = anchor.get_text(" ", strip=True)
            results_by_url[url] = {
                "title": title,
                "url": url,
                "description": snippet or f"Trusted learning resource for {topic}.",
                "type": infer_resource_type(url, title, snippet),
                "difficulty": infer_difficulty(title, snippet),
                "is_free": True,
                "rating": 4,
                "topics": [topic],
                "source": "web",
                "domain": normalize_domain(url),
            }

            if len(results_by_url) >= MAX_WEB_RESULTS:
                break
        if len(results_by_url) >= MAX_WEB_RESULTS or len(results_by_url) >= MIN_CANDIDATES_FOR_EARLY_STOP:
            break

    return list(results_by_url.values())


def scrape_search_query(topic: str, query: str) -> List[Dict]:
    headers = {
        "User-Agent": "Mozilla/5.0 PathLearner Resource Retriever",
    }
    response = requests.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers=headers,
        timeout=SEARCH_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    candidates = []
    seen_urls = set()
    for anchor in soup.select("a.result__a"):
        url = extract_result_url(anchor.get("href", ""))
        if not url or not is_trusted_domain(url) or url in seen_urls:
            continue

        block = anchor.find_parent(class_="result")
        snippet = ""
        if block:
            snippet_node = block.select_one(".result__snippet")
            if snippet_node:
                snippet = snippet_node.get_text(" ", strip=True)

        title = anchor.get_text(" ", strip=True)
        candidates.append(build_live_resource_candidate(topic, url, title, snippet))
        seen_urls.add(url)
    return candidates


def get_live_resource_candidates(
    topic: str,
    pricing: Optional[str],
    resource_type: Optional[str],
    difficulty: Optional[str],
) -> List[Dict]:
    if TAVILY_API_KEY:
        try:
            return tavily_search_trusted_web_resources(
                topic,
                pricing,
                resource_type,
                difficulty,
            )
        except requests.RequestException:
            pass

    return search_trusted_web_resources(
        topic,
        pricing,
        resource_type,
        difficulty,
    )


def ai_rerank_real_resources(topic: str, progress: Optional[str], candidates: List[Dict]) -> List[Dict]:
    if not candidates:
        return []

    candidate_lines = []
    for index, candidate in enumerate(candidates):
        candidate_lines.append(
            f"{index}. title={candidate['title']} | type={candidate['type']} | "
            f"difficulty={candidate['difficulty']} | domain={candidate.get('domain', 'catalog')} | "
            f"url={candidate['url']} | description={candidate['description']}"
        )

    prompt = (
        "You are ranking real learning resources for a learner.\n"
        "You must ONLY choose from the provided candidates. Do not invent titles or URLs.\n"
        f"Topic: {topic}\n"
        f"Learner progress: {progress or 'unknown'}\n"
        f"Pick the {FINAL_RESOURCE_COUNT} most relevant resources. Prefer relevance first, then beginner-friendliness for early learners, "
        "and avoid redundant results.\n"
        "Return ONLY valid JSON in this format:\n"
        '[{"index": 0, "why": "short reason"}]\n'
        "Candidates:\n"
        + "\n".join(candidate_lines)
    )

    try:
        raw = generate_text(prompt, max_tokens=500, temperature=0.1)
        parsed = json.loads(raw)
    except Exception:
        return candidates[:FINAL_RESOURCE_COUNT]

    reranked = []
    seen_indices = set()
    for item in parsed:
        index = item.get("index")
        if not isinstance(index, int) or index < 0 or index >= len(candidates) or index in seen_indices:
            continue
        seen_indices.add(index)
        candidate = dict(candidates[index])
        candidate["why"] = item.get("why") or candidate.get("why")
        reranked.append(candidate)

    if not reranked:
        return candidates[:FINAL_RESOURCE_COUNT]
    return reranked[:FINAL_RESOURCE_COUNT]


def ensure_resource_seeded(session: Session):
    existing = session.scalar(select(Resource.id).limit(1))
    if existing is not None:
        return

    for resource in CURATED_RESOURCES:
        session.add(
            Resource(
                slug=resource["slug"],
                title=resource["title"],
                url=resource["url"],
                description=resource["description"],
                resource_type=resource["resource_type"],
                difficulty=resource["difficulty"],
                is_free=resource["is_free"],
                rating=resource["rating"],
                topics_json=json.dumps(resource["topics"]),
            )
        )
    session.commit()


def ensure_resource_embeddings(session: Session, resources: List[Resource]):
    missing = [resource for resource in resources if not resource.embedding_json]
    if not missing:
        return

    embeddings = []
    for resource in missing:
        payload = {
            "title": resource.title,
            "description": resource.description,
            "topics": json.loads(resource.topics_json or "[]"),
            "resource_type": resource.resource_type,
            "difficulty": resource.difficulty,
        }
        embeddings.append(get_embedding(resource_embedding_text(payload)))

    for resource, embedding in zip(missing, embeddings):
        resource.embedding_json = json.dumps(embedding)

    session.commit()


def difficulty_bonus(progress: Optional[str], difficulty: str) -> float:
    if progress in (None, "", "not-started"):
        return {
            "beginner": 0.18,
            "intermediate": 0.03,
            "advanced": -0.16,
        }.get(difficulty, 0.0)
    if progress == "in-progress":
        return {
            "beginner": 0.08,
            "intermediate": 0.1,
            "advanced": -0.04,
        }.get(difficulty, 0.0)
    return {
        "beginner": 0.02,
        "intermediate": 0.06,
        "advanced": 0.12,
    }.get(difficulty, 0.0)


def beginner_intent_bonus(
    topic: str,
    title: str,
    description: str,
    resource_type: str,
    progress: Optional[str],
) -> float:
    if progress not in (None, "", "not-started"):
        return 0.0

    beginner_signals = [
        "introduction",
        "intro",
        "basics",
        "fundamentals",
        "beginner",
        "overview",
    ]
    haystack = " ".join(
        [
            title.lower(),
            description.lower(),
            resource_type.lower(),
        ]
    )
    signal_hits = sum(1 for signal in beginner_signals if signal in haystack)
    if signal_hits == 0:
        return 0.0

    topic_is_broad = len(topic.lower().split()) <= 4
    return 0.08 * signal_hits + (0.04 if topic_is_broad else 0.0)


def topic_overlap_bonus(topic: str, resource_topics: List[str]) -> float:
    topic_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", topic.lower())
        if len(token) > 2
    }
    if not topic_tokens:
        return 0.0

    resource_tokens = set()
    for entry in resource_topics:
        resource_tokens.update(
            token for token in re.findall(r"[a-z0-9]+", entry.lower()) if len(token) > 2
        )

    overlap = len(topic_tokens & resource_tokens)
    return min(overlap * 0.08, 0.24)


def repetition_penalty(title: str, seen_titles: Optional[List[str]]) -> float:
    if not seen_titles:
        return 0.0

    normalized_seen = {title.strip().lower() for title in seen_titles}
    return -0.28 if title.strip().lower() in normalized_seen else 0.0


def is_seen_resource(resource: Resource, seen_titles: Optional[List[str]]) -> bool:
    if not seen_titles:
        return False

    normalized_seen = {title.strip().lower() for title in seen_titles}
    return resource.title.strip().lower() in normalized_seen


def explain_match(topic: str, resource: Resource, similarity: float) -> str:
    topics = json.loads(resource.topics_json or "[]")
    matched_topic = next(
        (candidate for candidate in topics if candidate.lower() in topic.lower() or topic.lower() in candidate.lower()),
        None,
    )
    if matched_topic:
        return f"Matches the topic '{matched_topic}' and is strong on {resource.resource_type} learning."
    if similarity > 0.78:
        return f"Semantically very close to '{topic}' and rated highly for {resource.difficulty} learners."
    return f"Useful background material related to '{topic}' with a {resource.resource_type} format."


def score_candidate(
    candidate: Dict,
    topic: str,
    progress: Optional[str],
    query_embedding: List[float],
) -> Dict:
    similarity = cosine_similarity(query_embedding, candidate["embedding"])
    topics = candidate["topics"]
    keyword_bonus = 0.08 if any(topic.lower() in entry.lower() or entry.lower() in topic.lower() for entry in topics) else 0.0
    overlap_bonus = topic_overlap_bonus(topic, topics)
    free_bonus = 0.03 if candidate["is_free"] else 0.0
    rating_bonus = min(candidate["rating"] / 100.0, 0.05)
    score = (
        similarity
        + keyword_bonus
        + overlap_bonus
        + free_bonus
        + rating_bonus
        + difficulty_bonus(progress, candidate["difficulty"])
    )

    if candidate.get("source") == "catalog":
        score += beginner_intent_bonus(
            topic,
            candidate["title"],
            candidate["description"],
            candidate["type"],
            progress,
        )

    return {
        "similarity": similarity,
        "keyword_bonus": keyword_bonus,
        "overlap_bonus": overlap_bonus,
        "score": score,
    }


def candidate_matches_filters(
    candidate: Dict,
    pricing: Optional[str],
    resource_type: Optional[str],
    difficulty: Optional[str],
) -> bool:
    if pricing == "free" and not candidate["is_free"]:
        return False
    if pricing == "paid" and candidate["is_free"]:
        return False
    if resource_type and resource_type != "any" and candidate["type"] != resource_type:
        return False
    if difficulty and difficulty != "any" and candidate["difficulty"] != difficulty:
        return False
    return True


def recommend_resources(
    session: Session,
    topic: str,
    progress: Optional[str],
    seen_titles: Optional[List[str]],
    pricing: Optional[str],
    resource_type: Optional[str],
    difficulty: Optional[str],
):
    ensure_resource_seeded(session)
    db_resources = session.scalars(select(Resource)).all()
    ensure_resource_embeddings(session, db_resources)

    try:
        web_resources = get_live_resource_candidates(
            topic,
            pricing,
            resource_type,
            difficulty,
        )
        web_embeddings = get_embeddings(
            [
                resource_embedding_text(
                    {
                        "title": resource["title"],
                        "description": resource["description"],
                        "topics": resource["topics"],
                        "resource_type": resource["type"],
                        "difficulty": resource["difficulty"],
                    }
                )
                for resource in web_resources
            ]
        )
    except Exception:
        web_resources = []
        web_embeddings = []

    query_embedding = get_embedding(topic)
    seen_title_set = {title.strip().lower() for title in (seen_titles or [])}
    all_candidates = []

    for resource in db_resources:
        all_candidates.append(
            {
                "title": resource.title,
                "url": resource.url,
                "description": resource.description,
                "type": resource.resource_type,
                "difficulty": resource.difficulty,
                "is_free": resource.is_free == "true",
                "rating": resource.rating,
                "why": explain_match(
                    topic,
                    resource,
                    cosine_similarity(query_embedding, json.loads(resource.embedding_json or "[]")),
                ),
                "source": "catalog",
                "domain": normalize_domain(resource.url),
                "topics": json.loads(resource.topics_json or "[]"),
                "embedding": json.loads(resource.embedding_json or "[]"),
                "already_seen": is_seen_resource(resource, seen_titles),
            }
        )

    for resource, embedding in zip(web_resources, web_embeddings):
        all_candidates.append(
            {
                **resource,
                "why": f"Trusted web result from {resource['domain']} that appears relevant to '{topic}'.",
                "embedding": embedding,
                "already_seen": resource["title"].strip().lower() in seen_title_set,
            }
        )

    def build_shortlist(active_pricing, active_type, active_difficulty, enforce_threshold: bool = True):
        ranked = []
        repeated_ranked = []

        for candidate in all_candidates:
            if not candidate_matches_filters(candidate, active_pricing, active_type, active_difficulty):
                continue

            score_bits = score_candidate(candidate, topic, progress, query_embedding)
            similarity = score_bits["similarity"]
            keyword_bonus = score_bits["keyword_bonus"]
            overlap_bonus = score_bits["overlap_bonus"]
            score = score_bits["score"]

            if enforce_threshold and similarity < 0.42 and overlap_bonus == 0.0 and keyword_bonus == 0.0:
                continue

            material = {
                key: value
                for key, value in candidate.items()
                if key not in {"embedding", "topics", "already_seen"}
            }
            material["similarity"] = round(similarity, 4)
            material["score"] = score + (
                repetition_penalty(candidate["title"], seen_titles)
                if candidate["already_seen"] else 0.0
            )

            bucket = repeated_ranked if candidate["already_seen"] else ranked
            bucket.append(material)

        ranked.sort(key=lambda item: item["score"], reverse=True)
        repeated_ranked.sort(key=lambda item: item["score"], reverse=True)

        shortlist = ranked[:SHORTLIST_SIZE]
        if len(shortlist) < SHORTLIST_SIZE:
            shortlist.extend(repeated_ranked[: SHORTLIST_SIZE - len(shortlist)])
        return shortlist

    filter_attempts = [
        (pricing, resource_type, difficulty),
        (pricing, resource_type, "any"),
        (pricing, "any", difficulty),
        ("any", resource_type, difficulty),
        (pricing, "any", "any"),
        ("any", resource_type, "any"),
        ("any", "any", difficulty),
        ("any", "any", "any"),
    ]

    shortlist = []
    exact_match_results = build_shortlist(pricing, resource_type, difficulty)
    if exact_match_results:
        shortlist = exact_match_results

    for active_pricing, active_type, active_difficulty in filter_attempts:
        if shortlist and (active_pricing, active_type, active_difficulty) != (pricing, resource_type, difficulty):
            break
        shortlist = build_shortlist(active_pricing, active_type, active_difficulty)
        if shortlist:
            break

    if not shortlist:
        shortlist = build_shortlist(pricing, resource_type, difficulty, enforce_threshold=False)

    if not shortlist:
        shortlist = build_shortlist("any", "any", "any", enforce_threshold=False)

    if not shortlist:
        return []

    return ai_rerank_real_resources(topic, progress, shortlist)


def material_from_candidate(candidate: Dict, seen_titles: Optional[List[str]], score: float, similarity: float) -> Dict:
    material = {
        key: value
        for key, value in candidate.items()
        if key not in {"embedding", "topics", "already_seen"}
    }
    material["similarity"] = round(similarity, 4)
    material["score"] = score + (
        repetition_penalty(candidate["title"], seen_titles)
        if candidate.get("already_seen") else 0.0
    )
    return material


async def stream_recommendation_events(
    session: Session,
    topic: str,
    progress: Optional[str],
    seen_titles: Optional[List[str]],
    pricing: Optional[str],
    resource_type: Optional[str],
    difficulty: Optional[str],
):
    ensure_resource_seeded(session)
    db_resources = session.scalars(select(Resource)).all()
    ensure_resource_embeddings(session, db_resources)

    query_embedding = get_embedding(topic)
    seen_title_set = {title.strip().lower() for title in (seen_titles or [])}
    emitted_titles = set()
    collected_materials: List[Dict] = []

    def maybe_emit(candidate: Dict, active_pricing, active_type, active_difficulty, enforce_threshold=True):
        if not candidate_matches_filters(candidate, active_pricing, active_type, active_difficulty):
            return None

        score_bits = score_candidate(candidate, topic, progress, query_embedding)
        similarity = score_bits["similarity"]
        keyword_bonus = score_bits["keyword_bonus"]
        overlap_bonus = score_bits["overlap_bonus"]
        score = score_bits["score"]

        if enforce_threshold and similarity < 0.42 and overlap_bonus == 0.0 and keyword_bonus == 0.0:
            return None

        material = material_from_candidate(candidate, seen_titles, score, similarity)
        title_key = material["title"].strip().lower()
        if title_key in emitted_titles:
            return None
        emitted_titles.add(title_key)
        collected_materials.append(material)
        return material

    # Emit the strongest catalog matches first so the UI starts filling immediately.
    catalog_candidates = []
    for resource in db_resources:
        catalog_candidates.append(
            {
                "title": resource.title,
                "url": resource.url,
                "description": resource.description,
                "type": resource.resource_type,
                "difficulty": resource.difficulty,
                "is_free": resource.is_free == "true",
                "rating": resource.rating,
                "why": explain_match(
                    topic,
                    resource,
                    cosine_similarity(query_embedding, json.loads(resource.embedding_json or "[]")),
                ),
                "source": "catalog",
                "domain": normalize_domain(resource.url),
                "topics": json.loads(resource.topics_json or "[]"),
                "embedding": json.loads(resource.embedding_json or "[]"),
                "already_seen": is_seen_resource(resource, seen_titles),
            }
        )

    initial_materials = []
    for candidate in catalog_candidates:
        material = maybe_emit(candidate, pricing, resource_type, difficulty)
        if material:
            initial_materials.append(material)
    initial_materials.sort(key=lambda item: item["score"], reverse=True)
    for material in initial_materials[:6]:
        yield f"data: {json.dumps({'type': 'resource', 'resource': material})}\n\n"
        await asyncio.sleep(0)

    queries = limited_resource_queries(topic, pricing, resource_type, difficulty)[:MAX_QUERY_COUNT]
    results_by_url = set()
    for query in queries:
        try:
            live_candidates = (
                tavily_search_query(topic, query)
                if TAVILY_API_KEY
                else scrape_search_query(topic, query)
            )
        except requests.RequestException:
            continue

        if not live_candidates:
            continue

        unique_candidates = []
        for candidate in live_candidates:
            if candidate["url"] in results_by_url:
                continue
            results_by_url.add(candidate["url"])
            unique_candidates.append(candidate)

        embeddings = get_embeddings(
            [
                resource_embedding_text(
                    {
                        "title": candidate["title"],
                        "description": candidate["description"],
                        "topics": candidate["topics"],
                        "resource_type": candidate["type"],
                        "difficulty": candidate["difficulty"],
                    }
                )
                for candidate in unique_candidates
            ]
        )

        for candidate, embedding in zip(unique_candidates, embeddings):
            candidate["embedding"] = embedding
            candidate["already_seen"] = candidate["title"].strip().lower() in seen_title_set
            candidate["why"] = f"Trusted live result from {candidate['domain']} relevant to '{topic}'."
            material = maybe_emit(candidate, pricing, resource_type, difficulty)
            if material:
                yield f"data: {json.dumps({'type': 'resource', 'resource': material})}\n\n"
                await asyncio.sleep(0)
                if len(collected_materials) >= FINAL_RESOURCE_COUNT:
                    break

        if len(collected_materials) >= FINAL_RESOURCE_COUNT:
            break

    if not collected_materials:
        fallback = recommend_resources(
            session,
            topic,
            progress,
            seen_titles,
            pricing,
            resource_type,
            difficulty,
        )
        for item in fallback:
            yield f"data: {json.dumps({'type': 'resource', 'resource': item})}\n\n"
            await asyncio.sleep(0)
        yield f"data: {json.dumps({'type': 'done', 'resources': fallback})}\n\n"
        return

    final_resources = ai_rerank_real_resources(
        topic,
        progress,
        sorted(collected_materials, key=lambda item: item["score"], reverse=True)[:SHORTLIST_SIZE],
    )
    yield f"data: {json.dumps({'type': 'done', 'resources': final_resources})}\n\n"


def generate_roadmap_prompt(goal, hours, background):
    background_line = f'Background: "{background}".'
    if isinstance(background, str) and "Diagnostic assessment insight:" in background:
        background_line = f'Learner profile: "{background}".'
    return f"""
You are an expert curriculum designer.

Create a learning roadmap for: "{goal}".
{background_line}
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

Create 8-12 topics in a clear beginner-to-advanced sequence.
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
Explain "{topic}" in 2-3 simple beginner-friendly sentences.

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

Identify up to 3 missing prerequisite topics the user must learn BEFORE "{topic}".

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


def diagnostic_quiz_prompt(goal: str, background: str) -> str:
    return f"""
You are creating a short diagnostic assessment for a learner.

Learning goal: "{goal}"
Background: "{background}"

Create 5 multiple-choice questions that test the most important prerequisite knowledge
someone should have before starting this learning goal.
Use short scenario-based questions when possible, not pure definition recall.
Each question should target a distinct subskill or prerequisite concept.

Return ONLY valid JSON in this format:
{{
  "title": "Diagnostic Assessment",
  "questions": [
    {{
      "id": "q1",
      "question": "Question text",
      "options": ["A", "B", "C", "D"],
      "correct_index": 0,
      "explanation": "Short explanation",
      "difficulty": "beginner",
      "subskill": "Missing concept being tested"
    }}
  ]
}}

Keep the questions practical and foundational.
"""


def topic_quiz_prompt(
    topic: str,
    level: str,
    goal: str,
    background: str,
    prior_summary: str,
) -> str:
    return f"""
You are creating a short quiz for a learning roadmap topic.

Topic: "{topic}"
Roadmap level: "{level}"
Overall learning goal: "{goal}"
Learner background: "{background}"
Previous assessment signal: "{prior_summary}"

Create 4 multiple-choice questions that test whether the learner actually understands
this topic well enough to continue.
Make the questions scenario-based and application-oriented instead of definition-only.
Use an adaptive spread of difficulties:
- include at least 1 easier confidence-building question
- include 2 medium practical questions
- include 1 harder transfer question if prior performance was medium/high
Each question must test a specific subskill.

Return ONLY valid JSON in this format:
{{
  "title": "Topic Quiz",
  "questions": [
    {{
      "id": "q1",
      "question": "Question text",
      "options": ["A", "B", "C", "D"],
      "correct_index": 0,
      "explanation": "Short explanation",
      "difficulty": "beginner",
      "subskill": "Exact concept being tested"
    }}
  ]
}}
"""


def quiz_remediation_prompt(goal: str, topic: str, score_percent: int, weak_subskills: List[str]) -> str:
    return f"""
The learner is following a roadmap toward "{goal}".
They took a quiz on "{topic}" and scored {score_percent}%.
Weak subskills detected: {weak_subskills or ["general topic foundations"]}

Suggest up to 3 short prerequisite or reinforcement topics that should be inserted
before "{topic}" so the learner can recover.
Each suggested topic should directly target one of the weak subskills.

Return ONLY valid JSON:
[
  {{
    "id": "unique_slug",
    "title": "Reinforcement topic",
    "description": "1 sentence on why this helps",
    "estimated_hours": 2
  }}
]
"""


def extract_json_object(text: str) -> Dict:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise HTTPException(status_code=502, detail="Could not parse quiz JSON.")
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="Quiz JSON was invalid.") from exc


def extract_json_array(text: str) -> List[Dict]:
    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        raise HTTPException(status_code=502, detail="Could not parse remediation JSON.")
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="Remediation JSON was invalid.") from exc


def ensure_user(session: Session, client_id: str) -> User:
    user = session.scalar(select(User).where(User.client_id == client_id))
    if user:
        return user

    user = User(client_id=client_id)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def roadmap_level(position: int, total_items: int) -> str:
    if total_items <= 2:
        return "basic" if position == 0 else "advanced"

    ratio = position / max(total_items - 1, 1)
    if ratio < 0.34:
        return "basic"
    if ratio < 0.67:
        return "intermediate"
    return "advanced"


def confidence_label(score: Optional[int]) -> str:
    if score is None:
        return "unknown"
    if score < 50:
        return "low"
    if score < 75:
        return "medium"
    return "high"


def latest_confidence_by_item(session: Session, roadmap_id: str) -> Dict[str, int]:
    attempts = session.scalars(
        select(QuizAttempt)
        .where(
            QuizAttempt.roadmap_id == roadmap_id,
            QuizAttempt.quiz_type == "topic",
        )
        .order_by(QuizAttempt.created_at.desc())
    ).all()

    confidence_map: Dict[str, int] = {}
    for attempt in attempts:
        if not attempt.roadmap_item_id or attempt.roadmap_item_id in confidence_map:
            continue
        confidence_map[attempt.roadmap_item_id] = attempt.confidence_score
    return confidence_map


def serialize_quiz_session(session: QuizSession) -> Dict:
    raw_questions = json.loads(session.questions_json or "[]")
    public_questions = []
    for question in raw_questions:
        public_questions.append(
            {
                "id": question.get("id"),
                "question": question.get("question"),
                "options": question.get("options", []),
                "difficulty": question.get("difficulty", "beginner"),
                "subskill": question.get("subskill", "general understanding"),
            }
        )

    return {
        "session_id": session.id,
        "quiz_type": session.quiz_type,
        "title": session.title,
        "topic": session.topic,
        "questions": public_questions,
    }


def create_quiz_session(
    session: Session,
    client_id: str,
    quiz_type: str,
    topic: str,
    title: str,
    questions: List[Dict],
    roadmap_id: Optional[str] = None,
    roadmap_item_id: Optional[str] = None,
) -> QuizSession:
    quiz_session = QuizSession(
        client_id=client_id,
        roadmap_id=roadmap_id,
        roadmap_item_id=roadmap_item_id,
        quiz_type=quiz_type,
        topic=topic,
        title=title,
        questions_json=json.dumps(questions),
    )
    session.add(quiz_session)
    session.commit()
    session.refresh(quiz_session)
    return quiz_session


def question_weight(difficulty: str) -> float:
    return {
        "beginner": 1.0,
        "intermediate": 1.2,
        "advanced": 1.4,
    }.get((difficulty or "").lower(), 1.0)


def recent_attempts_for_topic(
    session: Session,
    client_id: str,
    topic: str,
    quiz_type: str,
    roadmap_item_id: Optional[str] = None,
) -> List[QuizAttempt]:
    stmt = (
        select(QuizAttempt)
        .where(
            QuizAttempt.client_id == client_id,
            QuizAttempt.topic == topic,
            QuizAttempt.quiz_type == quiz_type,
        )
        .order_by(QuizAttempt.created_at.desc())
    )
    attempts = session.scalars(stmt).all()
    if roadmap_item_id:
        item_matches = [attempt for attempt in attempts if attempt.roadmap_item_id == roadmap_item_id]
        if item_matches:
            return item_matches
    return attempts


def summarize_prior_attempts(attempts: List[QuizAttempt]) -> str:
    if not attempts:
        return "No previous attempts. Start with mostly beginner/intermediate questions."

    latest = attempts[0]
    previous_result = json.loads(latest.result_json or "{}")
    weak_subskills = previous_result.get("weak_subskills", [])
    trend = "improving" if len(attempts) > 1 and latest.score_percent >= attempts[1].score_percent else "flat"
    return (
        f"Latest score: {latest.score_percent}%. Confidence: {latest.confidence_score}."
        f" Weak subskills: {weak_subskills or ['none identified']}."
        f" Trend: {trend}. If latest score is low, lean easier before ramping up."
    )


def spaced_reassessment_days(score_percent: int) -> int:
    if score_percent < 50:
        return 2
    if score_percent < 75:
        return 5
    return 10


def build_quiz_result(raw_questions: List[Dict], answers: List[int]) -> Dict:
    total = len(raw_questions)
    reviewed_questions = []
    correct_count = 0
    weighted_total = 0.0
    weighted_correct = 0.0
    weak_subskill_scores: Dict[str, Dict[str, float]] = {}

    for index, question in enumerate(raw_questions):
        selected_index = answers[index] if index < len(answers) else None
        correct_index = question.get("correct_index")
        is_correct = selected_index == correct_index
        difficulty = question.get("difficulty", "beginner")
        weight = question_weight(difficulty)
        weighted_total += weight
        if is_correct:
            correct_count += 1
            weighted_correct += weight
        subskill = question.get("subskill") or "general understanding"
        bucket = weak_subskill_scores.setdefault(subskill, {"total": 0.0, "missed": 0.0})
        bucket["total"] += weight
        if not is_correct:
            bucket["missed"] += weight
        reviewed_questions.append(
            {
                "id": question.get("id"),
                "question": question.get("question"),
                "options": question.get("options", []),
                "selected_index": selected_index,
                "correct_index": correct_index,
                "correct_answer": question.get("options", [])[correct_index] if isinstance(correct_index, int) and correct_index < len(question.get("options", [])) else None,
                "is_correct": is_correct,
                "explanation": question.get("explanation", ""),
                "difficulty": difficulty,
                "subskill": subskill,
            }
        )

    score_percent = int(round((correct_count / total) * 100)) if total else 0
    confidence_score = int(round((weighted_correct / weighted_total) * 100)) if weighted_total else score_percent
    weak_subskills = [
        subskill
        for subskill, stats in sorted(
            weak_subskill_scores.items(),
            key=lambda item: item[1]["missed"] / max(item[1]["total"], 1.0),
            reverse=True,
        )
        if stats["missed"] > 0
    ][:3]
    return {
        "score_percent": score_percent,
        "confidence_score": confidence_score,
        "correct_count": correct_count,
        "total_questions": total,
        "review": reviewed_questions,
        "confidence_label": confidence_label(confidence_score),
        "weak_subskills": weak_subskills,
        "recommended_retake_in_days": spaced_reassessment_days(confidence_score),
    }


def apply_low_score_roadmap_update(
    session: Session,
    roadmap: Roadmap,
    current_topic: str,
    score_percent: int,
    weak_subskills: List[str],
) -> List[Dict]:
    if score_percent >= 60:
        return []

    raw = generate_text(
        quiz_remediation_prompt(roadmap.goal, current_topic, score_percent, weak_subskills),
        max_tokens=400,
        temperature=0.1,
    )
    suggestions = extract_json_array(raw)

    items = sorted(roadmap.items, key=lambda item: item.position)
    insert_at = next(
        (index for index, item in enumerate(items) if item.title == current_topic),
        len(items),
    )
    existing_titles = {item.title.strip().lower() for item in items}

    filtered = []
    for item in suggestions:
        title = (item.get("title") or "").strip()
        if not title:
            continue
        normalized = title.lower()
        if normalized in existing_titles:
            continue
        existing_titles.add(normalized)
        filtered.append(
            {
                "id": str(item.get("id") or f"quiz-pre-{uuid4()}"),
                "title": title,
                "description": item.get("description", ""),
                "estimated_hours": int(item.get("estimated_hours") or 2),
            }
        )

    if not filtered:
        return []

    for item in items:
        if item.position >= insert_at:
            item.position += len(filtered)

    for offset, item in enumerate(filtered):
        session.add(
            RoadmapItem(
                id=item["id"],
                roadmap_id=roadmap.id,
                position=insert_at + offset,
                title=item["title"],
                description=item["description"],
                estimated_hours=item["estimated_hours"],
                progress="not-started",
            )
        )

    roadmap.updated_at = datetime.utcnow()
    session.commit()
    return filtered


def serialize_roadmap(session: Session, roadmap: Roadmap):
    items = sorted(roadmap.items, key=lambda item: item.position)
    total_items = len(items)
    confidence_map = latest_confidence_by_item(session, roadmap.id)
    return {
        "roadmap_id": roadmap.id,
        "goal": roadmap.goal,
        "background": roadmap.background,
        "time_per_week_hours": roadmap.time_per_week_hours,
        "updated_at": roadmap.updated_at.isoformat(),
        "roadmap": [
            {
                "id": item.id,
                "title": item.title,
                "description": item.description,
                "estimated_hours": item.estimated_hours,
                "progress": item.progress,
                "level": roadmap_level(index, total_items),
                "confidence_score": confidence_map.get(item.id),
                "confidence_label": confidence_label(confidence_map.get(item.id)),
            }
            for index, item in enumerate(items)
        ],
    }


def save_roadmap(session: Session, user: User, req: GenerateRequest, roadmap_data: List[Dict]):
    roadmap = Roadmap(
        user_id=user.id,
        goal=req.goal,
        background=req.background,
        time_per_week_hours=req.time_per_week_hours,
    )
    session.add(roadmap)
    session.flush()

    for idx, item in enumerate(roadmap_data):
        raw_id = str(item.get("id") or "").strip()
        safe_id = raw_id or f"roadmap-item-{uuid4()}"
        session.add(
            RoadmapItem(
                id=f"{safe_id}-{uuid4().hex[:8]}",
                roadmap_id=roadmap.id,
                position=idx,
                title=item.get("title") or "Untitled",
                description=item.get("description", ""),
                estimated_hours=int(item.get("estimated_hours") or 3),
                progress="not-started",
            )
        )

    session.commit()
    session.refresh(roadmap)
    return roadmap


def get_latest_roadmap(session: Session, client_id: str) -> Optional[Roadmap]:
    user = session.scalar(select(User).where(User.client_id == client_id))
    if not user:
        return None

    return session.scalar(
        select(Roadmap)
        .where(Roadmap.user_id == user.id)
        .order_by(Roadmap.created_at.desc())
    )


def build_background_with_diagnostic(background: str, diagnostic_result: Optional[Dict]) -> str:
    if not diagnostic_result:
        return background

    readiness = diagnostic_result.get("readiness_level", "unknown")
    score = diagnostic_result.get("score_percent", 0)
    takeaway = diagnostic_result.get("takeaway", "")
    diagnostic_line = (
        f"Diagnostic assessment insight: learner scored {score}% with {readiness} readiness."
    )
    if takeaway:
        diagnostic_line += f" Key gap: {takeaway}"
    return f"{background}\n{diagnostic_line}".strip()


@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html>
        <head>
            <meta charset="UTF-8" />
            <link rel="icon" type="image/png" href="/Public/PathLearner.png" />
            <meta name="viewport" content="width=device-width, initial-scale=1.0" />
            <title>PathLearner Backend</title>
        </head>
        <body style="font-family: sans-serif; display:flex; align-items:center; justify-content:center; height:100vh; margin:0;">
            <div>
                <h1>PathLearner Backend</h1>
                <p>API is running.</p>
            </div>
        </body>
    </html>
    """


@app.post("/users/ensure")
async def ensure_user_route(req: EnsureUserRequest):
    db_engine = require_database()
    with Session(db_engine) as session:
        user = ensure_user(session, req.client_id)
        return {"client_id": user.client_id}


@app.get("/users/{client_id}/roadmaps/latest")
async def latest_roadmap(client_id: str):
    db_engine = require_database()
    with Session(db_engine) as session:
        roadmap = get_latest_roadmap(session, client_id)
        if roadmap is None:
            return {"roadmap": None}
        return {"roadmap": serialize_roadmap(session, roadmap)}


@app.post("/generate_roadmap")
async def generate_roadmap(req: GenerateRequest):
    learner_background = build_background_with_diagnostic(
        req.background,
        req.diagnostic_result,
    )
    prompt = generate_roadmap_prompt(req.goal, req.time_per_week_hours, learner_background)
    text = generate_text(prompt, max_tokens=900, temperature=0.2)

    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        return {"error": "Could not find JSON", "raw": text}

    data = json.loads(match.group(0))

    db_engine = require_database()
    with Session(db_engine) as session:
        user = ensure_user(session, req.client_id)
        roadmap = save_roadmap(session, user, req, data)
        return serialize_roadmap(session, roadmap)


@app.patch("/users/{client_id}/roadmaps/latest/items/{item_id}/progress")
async def update_progress(client_id: str, item_id: str, req: ProgressUpdateRequest):
    db_engine = require_database()
    with Session(db_engine) as session:
        roadmap = get_latest_roadmap(session, client_id)
        if roadmap is None:
            raise HTTPException(status_code=404, detail="No roadmap found for this user.")

        item = session.scalar(
            select(RoadmapItem)
            .where(RoadmapItem.roadmap_id == roadmap.id, RoadmapItem.id == item_id)
        )
        if item is None:
            raise HTTPException(status_code=404, detail="Roadmap item not found.")

        item.progress = req.progress
        roadmap.updated_at = datetime.utcnow()
        session.commit()
        return {"ok": True}


@app.post("/get_materials")
async def get_materials(req: MaterialsRequest):
    db_engine = require_database()
    with Session(db_engine) as session:
        resources = recommend_resources(
            session,
            req.topic,
            req.progress,
            req.seen_titles,
            req.pricing,
            req.resource_type,
            req.difficulty,
        )
        return {"resources": resources}


@app.get("/stream_materials")
async def stream_materials(
    topic: str,
    progress: Optional[str] = None,
    seen_titles: Optional[str] = None,
    pricing: Optional[str] = None,
    resource_type: Optional[str] = None,
    difficulty: Optional[str] = None,
):
    db_engine = require_database()
    parsed_seen_titles = []
    if seen_titles:
        try:
            parsed_seen_titles = json.loads(seen_titles)
        except json.JSONDecodeError:
            parsed_seen_titles = []

    async def event_stream():
        with Session(db_engine) as session:
            yield "event: ready\ndata: {}\n\n"
            await asyncio.sleep(0)
            async for chunk in stream_recommendation_events(
                session,
                topic,
                progress,
                parsed_seen_titles,
                pricing,
                resource_type,
                difficulty,
            ):
                yield chunk

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/get_summary")
async def get_summary(req: SummaryRequest):
    prompt = summary_prompt(req.topic)
    text = generate_text(prompt, max_tokens=300, temperature=0.2)

    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return {
            "title": req.topic,
            "summary": text.strip() or f"A short summary for {req.topic} is not available yet.",
            "raw": text,
        }

    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {
            "title": req.topic,
            "summary": text.strip() or f"A short summary for {req.topic} is not available yet.",
            "raw": text,
        }

    return {
        "title": parsed.get("title") or req.topic,
        "summary": parsed.get("summary") or text.strip() or f"A short summary for {req.topic} is not available yet.",
        "raw": text,
    }


@app.post("/diagnostic/generate")
async def generate_diagnostic(req: DiagnosticGenerateRequest):
    prompt = diagnostic_quiz_prompt(req.goal, req.background)
    raw = generate_text(prompt, max_tokens=900, temperature=0.2)
    parsed = extract_json_object(raw)
    questions = parsed.get("questions", [])
    if not questions:
        raise HTTPException(status_code=502, detail="Diagnostic quiz generation returned no questions.")

    db_engine = require_database()
    with Session(db_engine) as session:
        quiz_session = create_quiz_session(
            session,
            client_id=req.client_id,
            quiz_type="diagnostic",
            topic=req.goal,
            title=parsed.get("title") or "Diagnostic Assessment",
            questions=questions,
        )
        previous_attempts = recent_attempts_for_topic(session, req.client_id, req.goal, "diagnostic")
        payload = serialize_quiz_session(quiz_session)
        payload["attempt_history"] = [
            {
                "score_percent": attempt.score_percent,
                "confidence_score": attempt.confidence_score,
                "created_at": attempt.created_at.isoformat(),
            }
            for attempt in previous_attempts[:5]
        ]
        return payload


@app.post("/topic-quiz/generate")
async def generate_topic_quiz(req: TopicQuizGenerateRequest):
    db_engine = require_database()
    with Session(db_engine) as session:
        roadmap = get_latest_roadmap(session, req.client_id)
        roadmap_id = roadmap.id if roadmap else None
        previous_attempts = recent_attempts_for_topic(
            session,
            req.client_id,
            req.topic,
            "topic",
            req.roadmap_item_id,
        )
        prompt = topic_quiz_prompt(
            req.topic,
            req.level or "basic",
            req.goal or (roadmap.goal if roadmap else ""),
            req.background or (roadmap.background if roadmap else ""),
            summarize_prior_attempts(previous_attempts),
        )
        raw = generate_text(prompt, max_tokens=800, temperature=0.2)
        parsed = extract_json_object(raw)
        questions = parsed.get("questions", [])
        if not questions:
            raise HTTPException(status_code=502, detail="Topic quiz generation returned no questions.")

        quiz_session = create_quiz_session(
            session,
            client_id=req.client_id,
            quiz_type="topic",
            topic=req.topic,
            title=parsed.get("title") or f"{req.topic} Quiz",
            questions=questions,
            roadmap_id=roadmap_id,
            roadmap_item_id=req.roadmap_item_id,
        )
        payload = serialize_quiz_session(quiz_session)
        payload["attempt_history"] = [
            {
                "score_percent": attempt.score_percent,
                "confidence_score": attempt.confidence_score,
                "created_at": attempt.created_at.isoformat(),
                "weak_subskills": json.loads(attempt.result_json or "{}").get("weak_subskills", []),
            }
            for attempt in previous_attempts[:5]
        ]
        return payload


@app.post("/quiz/submit")
async def submit_quiz(req: QuizSubmitRequest):
    db_engine = require_database()
    with Session(db_engine) as session:
        quiz_session = session.scalar(
            select(QuizSession).where(
                QuizSession.id == req.session_id,
                QuizSession.client_id == req.client_id,
            )
        )
        if quiz_session is None:
            raise HTTPException(status_code=404, detail="Quiz session not found.")

        raw_questions = json.loads(quiz_session.questions_json or "[]")
        result = build_quiz_result(raw_questions, req.answers)

        attempt = QuizAttempt(
            session_id=quiz_session.id,
            client_id=req.client_id,
            roadmap_id=quiz_session.roadmap_id,
            roadmap_item_id=quiz_session.roadmap_item_id,
            quiz_type=quiz_session.quiz_type,
            topic=quiz_session.topic,
            score_percent=result["score_percent"],
            confidence_score=result["confidence_score"],
            answers_json=json.dumps(req.answers),
            result_json=json.dumps(result),
        )
        session.add(attempt)
        session.commit()

        payload = {
            "quiz_type": quiz_session.quiz_type,
            **result,
        }

        if quiz_session.quiz_type == "diagnostic":
            payload["readiness_level"] = (
                "advanced"
                if result["score_percent"] >= 80
                else "intermediate"
                if result["score_percent"] >= 55
                else "beginner"
            )
            weakest = next((item for item in result["review"] if not item["is_correct"]), None)
            payload["takeaway"] = (
                weakest["explanation"]
                if weakest and weakest.get("explanation")
                else "Foundational review recommended before starting."
            )
            payload["attempt_history"] = [
                {
                    "score_percent": attempt.score_percent,
                    "confidence_score": attempt.confidence_score,
                    "created_at": attempt.created_at.isoformat(),
                }
                for attempt in recent_attempts_for_topic(session, req.client_id, quiz_session.topic, "diagnostic")[:5]
            ]
            return payload

        roadmap = None
        inserted_topics: List[Dict] = []
        if quiz_session.roadmap_id:
            roadmap = session.scalar(select(Roadmap).where(Roadmap.id == quiz_session.roadmap_id))
            if roadmap is not None:
                inserted_topics = apply_low_score_roadmap_update(
                    session,
                    roadmap,
                    quiz_session.topic,
                    result["score_percent"],
                    result["weak_subskills"],
                )

        payload["roadmap_updated"] = bool(inserted_topics)
        payload["inserted_topics"] = inserted_topics
        payload["attempt_history"] = [
            {
                "score_percent": attempt.score_percent,
                "confidence_score": attempt.confidence_score,
                "created_at": attempt.created_at.isoformat(),
                "weak_subskills": json.loads(attempt.result_json or "{}").get("weak_subskills", []),
            }
            for attempt in recent_attempts_for_topic(
                session,
                req.client_id,
                quiz_session.topic,
                "topic",
                quiz_session.roadmap_item_id,
            )[:5]
        ]
        if roadmap is not None:
            session.refresh(roadmap)
            payload["roadmap"] = serialize_roadmap(session, roadmap)
        return payload

    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {
            "title": req.topic,
            "summary": "No summary generated.",
            "raw": text,
        }


@app.post("/handle_confusion")
async def handle_confusion(req: ConfusionRequest):
    prompt = confusion_prompt(
        req.goal,
        req.roadmap,
        req.current_topic,
        req.confusion_text,
    )
    text = generate_text(prompt, max_tokens=700, temperature=0.1)

    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        return {"new_prereqs": [], "raw": text}

    data = json.loads(match.group(0))
    cleaned = [
        {
            "id": str(item.get("id") or f"pre-{uuid4()}"),
            "title": item.get("title") or "Missing prerequisite",
            "description": item.get("description", ""),
            "estimated_hours": int(item.get("estimated_hours") or 3),
        }
        for item in data
    ]

    db_engine = require_database()
    with Session(db_engine) as session:
        roadmap = get_latest_roadmap(session, req.client_id)
        if roadmap is None:
            raise HTTPException(status_code=404, detail="No roadmap found for this user.")

        items = sorted(roadmap.items, key=lambda item: item.position)
        insert_at = next(
            (index for index, item in enumerate(items) if item.title == req.current_topic),
            len(items),
        )

        existing_titles = {item.title.strip().lower() for item in items}
        filtered = []
        for item in cleaned:
            normalized = item["title"].strip().lower()
            if normalized in existing_titles:
                continue
            existing_titles.add(normalized)
            filtered.append(item)

        if filtered:
            for index, item in enumerate(items):
                if item.position >= insert_at:
                    item.position += len(filtered)

            for offset, item in enumerate(filtered):
                session.add(
                    RoadmapItem(
                        id=item["id"],
                        roadmap_id=roadmap.id,
                        position=insert_at + offset,
                        title=item["title"],
                        description=item["description"],
                        estimated_hours=item["estimated_hours"],
                        progress="not-started",
                    )
                )

            roadmap.updated_at = datetime.utcnow()
            session.commit()

        return {"new_prereqs": filtered}

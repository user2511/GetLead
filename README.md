# LeadFlow 🤖

> AI-powered WhatsApp agent that captures leads, books appointments,
> sends reminders, and follows up — autonomously, 24/7.

## 🎬 Demo

[Insert your Loom video link here]

> Real conversation: Customer books a dental appointment 
> entirely through WhatsApp in under 2 minutes.

## 🏗️ Architecture

\```
Customer WhatsApp Message
         │
         ▼
  Twilio Webhook → FastAPI
         │
         ▼
  LangGraph Pipeline
  ├── Agent 1: Intent Classifier (Groq Llama 3.1)
  │     └── Classifies: book / reschedule / FAQ / emergency
  ├── Agent 2: Booking Agent (Cal.com v2 API)  
  │     └── Multi-turn conversation → real appointment created
  ├── Agent 3: Follow-up Agent (APScheduler)
  │     └── 24hr reminder / 1hr reminder / review request / no-show
  ├── Agent 4: Escalation Agent (Twilio notify)
  │     └── Emergency keywords → instant owner notification
  └── Agent 5: Response Agent (Groq)
        └── FAQ / greetings / general queries
         │
         ▼
  PostgreSQL (leads + bookings)
         │
         ▼
  Langfuse (full LLM observability)
         │
         ▼
  Render (production deployment)
\```

## ⚡ Tech Stack

| Layer | Technology |
|---|---|
| Agent Framework | LangGraph |
| LLM | Groq — Llama 3.1 70B |
| Messaging | Twilio WhatsApp API |
| Booking | Cal.com v2 API |
| Backend | FastAPI + Python |
| Database | PostgreSQL |
| Scheduling | APScheduler |
| Monitoring | Langfuse |
| Deployment | Render + Docker |
| CI/CD | GitHub Actions |

## 🤖 What It Does

1. Customer sends WhatsApp message
2. AI classifies intent instantly
3. Guides customer through booking in natural conversation
4. Creates real appointment on Cal.com
5. Sends 24hr + 1hr reminders automatically
6. Requests Google review after visit
7. Follows up on no-shows
8. Escalates emergencies to owner immediately

## 🏪 Works For Any Local Business

Configure for any business via one JSON file:
- 🦷 Dental Clinics
- 🔧 Plumbers  
- 💅 Salons
- 🏥 Medical Clinics
- 💪 Gyms

## 🚀 Quick Start

\```bash
git clone https://github.com/YOURNAME/leadflow
cd leadflow
cp .env.example .env
# Fill in API keys
docker-compose up --build
\```

## 📊 Results

- ✅ Handles full booking conversation autonomously
- ✅ Integrated with real Cal.com calendar
- ✅ 4 automated follow-up messages per booking
- ✅ Zero missed leads — responds 24/7
- ✅ Deployed and production ready

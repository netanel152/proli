# Fixi System Architecture

This document provides a high-level overview of the Fixi system architecture, component interactions, and data flow.

## 1. High-Level Overview

Fixi acts as an intelligent intermediary between Clients (WhatsApp Users) and Professionals. It utilizes a microservices-like architecture (within a monorepo) orchestrated by Docker Compose.

### Core Components

1.  **Backend API (FastAPI):**
    *   **Role:** Entry point for external Webhooks (Green API) and internal health checks.
    *   **Responsibility:** Rapid ingestion of messages, request validation, and queuing tasks to the Worker.
    *   **Scaling:** Stateless, can be horizontally scaled behind a load balancer.

2.  **Background Worker (ARQ + APScheduler):**
    *   **Role:** The heavy lifter of the system.
    *   **Responsibilities:**
        *   **Message Processing (ARQ):** Handles AI inference, DB operations, and WhatsApp API calls asynchronously.
        *   **Periodic Tasks (APScheduler):** Runs "Good Morning" reminders, Stale Job Monitors, and SOS Auto-Healing.
    *   **Scaling:** Can be scaled horizontally (multiple worker containers) pointing to the same Redis instance.

3.  **Admin Panel (Streamlit):**
    *   **Role:** Management Interface.
    *   **Responsibility:** Visualization of leads, managing professionals, and system configuration.
    *   **Security:** Protected by Cookie-based Auth with Bcrypt hashing.

4.  **Database Layer:**
    *   **MongoDB Atlas:** Primary data store (Users, Leads, Settings).
    *   **Redis:** Fast-access layer for:
        *   **Task Queue:** ARQ job backend.
        *   **Context:** Recent chat history (for AI continuity).
        *   **State:** User session state (Finite State Machine).

## 2. Detailed Data Flow

### A. Inbound Message Flow (The "Fast Path")
1.  **User** sends WhatsApp message.
2.  **Green API** pushes JSON webhook to `POST /webhook` on **Backend API**.
3.  **Backend API:**
    *   Validates payload (Security check).
    *   Enqueues `process_message_task` to **Redis** via **ARQ**.
    *   Returns `200 OK` immediately (preventing timeout).

### B. Message Processing (The "Smart Path")
1.  **Worker** picks up `process_message_task`.
2.  **State Manager** checks if user is in a specific flow (e.g., `REQUIRE_MORE_INFO`).
3.  **Context Manager** fetches last 20 messages from Redis.
4.  **AI Engine (Gemini 2.5)**:
    *   Analyzes text/image/audio.
    *   Decides whether to:
        *   **Route:** Extract city/issue and find a Pro.
        *   **Converse:** Ask clarifying questions.
        *   **Book:** Confirm a time slot.
5.  **Matching Service** (if routing):
    *   Queries MongoDB for active Pros in the area.
    *   Applies Load Balancing (Max 3 active jobs).
    *   Selects the best Pro.
6.  **Action:**
    *   Updates MongoDB.
    *   Sends WhatsApp reply to User.
    *   Sends Notification to Pro (if applicable).

### C. Periodic Maintenance (The "Safety Net")
*   **Every 10 mins:** `SOS Healer` checks for leads stuck in `NEW` state > 30 mins and reassigns them.
*   **Every 30 mins:** `Stale Monitor` pings Pros about unfinished jobs.
*   **Daily (08:00):** Sends daily agenda to Pros.

## 3. Technology Stack Breakdown

| Component | Technology | Reasoning |
| :--- | :--- | :--- |
| **Language** | Python 3.12+ | Rich ecosystem for AI and Async Web. |
| **Web Framework** | FastAPI | High performance, native AsyncIO support. |
| **Async Worker** | ARQ | Lightweight, built on Redis, perfect for simple job queues. |
| **Scheduling** | APScheduler | Robust Cron-like scheduling for Python. |
| **Database** | MongoDB (Motor) | Schema-less flexibility for evolving Lead structures. |
| **Caching** | Redis | Low latency for context and state management. |
| **AI Model** | Gemini 2.5 Flash | Multimodal, fast, and cost-effective. |
| **Infrastructure** | Docker Compose | Simple orchestration for all services. |

## 4. Directory Structure Mapping

*   `app/api/`: Fast/Synchronous endpoints.
*   `app/services/`: Business Logic (AI, Matching, Workflow).
*   `app/core/`: Configuration & Infrastructure wrappers (DB, Redis, Logging).
*   `app/worker.py`: Worker process entry point.
*   `admin_panel/`: Streamlit UI code.


# Proli Backend API Documentation

## Base URL
Local: `http://localhost:8000`
Production: `https://your-domain.com`

## Endpoints

### 1. Health Check
**GET** `/`

Returns the operational status of the API.

**Response:**
```json
{
  "status": "Proli is running! 🚀"
}
```

### 2. WhatsApp Webhook
**POST** `/webhook`

The main entry point for receiving messages from Green API.

**Headers:**
- `Content-Type: application/json`

**Request Body (Green API Standard):**
```json
{
  "typeWebhook": "incomingMessageReceived",
  "instanceData": {
    "idInstance": 12345,
    "wid": "11001234567@c.us",
    "typeInstance": "whatsapp"
  },
  "timestamp": 1600000000,
  "idMessage": "BAE5...",
  "senderData": {
    "chatId": "972500000000@c.us",
    "sender": "972500000000@c.us",
    "senderName": "Israel Israeli"
  },
  "messageData": {
    "typeMessage": "textMessage",
    "textMessageData": {
      "textMessage": "Hi, I have a leak in the kitchen."
    }
  }
}
```

**Supported Message Types:**
- `textMessage`
- `extendedTextMessage`
- `imageMessage` (with `fileMessageData`)
- `videoMessage` (with `fileMessageData`)
- `audioMessage` (with `fileMessageData`)

**Responses:**
- `200 OK`: `{ "status": "processing_message" }` (Task queued)
- `200 OK`: `{ "status": "ignored_group" }` (Groups are ignored)
- `200 OK`: `{ "status": "ignored_no_data" }` (Malformed payload)
- `200 OK`: `{ "status": "ignored_type" }` (Unsupported webhook type)
- `200 OK`: `{ "status": "ignored_wrong_instance" }` (Security mismatch)
- `200 OK`: `{ "status": "error" }` (Internal handling error)

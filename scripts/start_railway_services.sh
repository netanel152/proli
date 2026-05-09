#!/bin/bash

# Default to staging if no environment argument is provided
ENV_NAME=${1:-staging}

if [ "$ENV_NAME" = "production" ]; then
  ENV_ID="a8c1fc4c-9434-48c4-9461-afce87651d21"
else
  ENV_ID="93e8ad7e-3582-4ab7-8f71-1775bf0bbddc"
fi

echo "Starting Proli services on Railway in environment: $ENV_NAME ($ENV_ID)..."
railway up --service api -e $ENV_ID -d
railway up --service worker -e $ENV_ID -d
railway up --service admin -e $ENV_ID -d
echo "Deployments triggered for $ENV_NAME."

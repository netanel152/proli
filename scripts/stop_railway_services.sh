#!/bin/bash

# Default to staging if no environment argument is provided
ENV_NAME=${1:-staging}

# PRO-128: environment names rather than hardcoded uuids — see the note in
# start_railway_services.sh. Both ids in this script were stale and dead.
if [ "$ENV_NAME" = "production" ]; then
  RAILWAY_ENV="Production"
else
  RAILWAY_ENV="Staging"
fi

echo "Stopping Proli services on Railway in environment: $RAILWAY_ENV..."
railway down --service api -e "$RAILWAY_ENV" -y
railway down --service worker -e "$RAILWAY_ENV" -y
railway down --service admin -e "$RAILWAY_ENV" -y
echo "Done stopping $RAILWAY_ENV."

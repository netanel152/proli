#!/bin/bash

# Default to staging if no environment argument is provided
ENV_NAME=${1:-staging}

# PRO-128: this script used to hardcode environment *IDs*, and both had gone
# stale — neither resolved any more, so every invocation failed outright. That
# is a plausible reason production was never deployed by hand for weeks. The
# Railway CLI accepts an environment *name* wherever it accepts an id, and the
# names are stable in a way the uuids demonstrably are not, so pass the name.
if [ "$ENV_NAME" = "production" ]; then
  RAILWAY_ENV="Production"
else
  RAILWAY_ENV="Staging"
fi

echo "Starting Proli services on Railway in environment: $RAILWAY_ENV..."
railway up --service api -e "$RAILWAY_ENV" -d
railway up --service worker -e "$RAILWAY_ENV" -d
railway up --service admin -e "$RAILWAY_ENV" -d
echo "Deployments triggered for $RAILWAY_ENV."

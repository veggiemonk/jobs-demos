#!/usr/bin/env bash

if [ -z "$1" ]; then
    echo "service name is not set. ==> ./deploy.sh <service-name>"
    exit 1
else
    svc=$(basename "$1")
    echo "deploying $svc"
    gcloud run deploy "$svc" --region us-central1 --source "$1"
fi






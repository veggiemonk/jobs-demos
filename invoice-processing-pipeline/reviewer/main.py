# Copyright 2022 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
    Web app to review information extracted from submitted invoices, and
    mark the result from the review.

    A separate app enables vendors to submit invoices, and the main Cloud Run
    Jobs app extracts the data.

    Requirements:

    -   Python 3.7 or later
    -   All packages in requirements.txt installed
    -   A bucket with the invoice files in the /processed folder
    -   Firestore database with information about those invoices
    -   Software environment has ADC or other credentials to read from the
        bucket (in order to display to the reviewer), and to read and write
        to the Firestore database (to display information and update status)
    -   The name of the bucket (not the URI) in the environment variable BUCKET

    This Flask app can be run directly via "python main.py" or with gunicorn
    or other WSGI web servers.
"""

from datetime import timedelta
import os

from flask import Flask, redirect, render_template, request

from google import auth
from google.auth.transport import requests
from google.cloud import firestore
from google.cloud import storage
import redis

BUCKET_NAME = os.environ.get("BUCKET")
REDISHOST = os.environ.get("REDISHOST", "localhost")
REDISPORT = int(os.environ.get("REDISPORT", 6379))
PROCESSED_PREFIX = "processed/"
APPROVED_PREFIX = "approved/"

app = Flask(__name__)
db = firestore.Client()
cache = redis.StrictRedis(host=REDISHOST, port=REDISPORT, decode_responses=True)
gcs = storage.Client()


@app.route("/reviewer", methods=["GET"])
def show_list_to_review2():
    return show_list_to_review()


@app.route("/reviewer", methods=["POST"])
def approve_selected_invoices2():
    return approve_selected_invoices()


# GET to / will return a list of processed invoices with data, links, and a form
@app.route("/", methods=["GET"])
def show_list_to_review():
    use_cache = request.args.get("cache")
    print(f"use_cache is {use_cache}")

    # use_caching = request.args.get("caching")
    # print(f"use_caching is {use_caching} ")

    # Get the number of times this page has been viewed
    # and the number of invoices that have been approved
    # from the Redis cache
    views = cache.incr("reviewer.show", 1)
    approved = cache.get("reviewer.approve")

    # Query the DB for all "Not Approved" invoices
    colref = db.collection("invoices")
    query = colref.where("state", "==", "Not Approved")

    # Build data list to work with and then render in a template
    invoices = [rec.to_dict() for rec in query.stream()]

    # Will need signed URLs in web page so users can see the PDFs
    # Prepare storage client to create those
    gcs = storage.Client()
    bucket = gcs.get_bucket(BUCKET_NAME)

    # Will need credentials to generate signed URLs
    credentials, _ = auth.default()
    if credentials.token is None:
        credentials.refresh(requests.Request())

    # Update the data list with signed URLs
    for invoice in invoices:
        full_name = f"{PROCESSED_PREFIX}{invoice['blob_name']}"
        print(f"Blob full name is {full_name}")
        blob = bucket.get_blob(full_name)

        # Add the URLs to the list
        url = "None"  # Fallback that should never be needed

        if blob is not None:
            url = None
            if use_cache:
                url = cache.get(f"reviewer.invoice.{full_name}")
            if url is None:
                url = blob.generate_signed_url(
                    version="v4",
                    expiration=timedelta(hours=1),
                    service_account_email=credentials.service_account_email,
                    access_token=credentials.token,
                    method="get",
                    scheme="https",
                )
                cache.set(f"reviewer.invoice.{full_name}", url, ex=timedelta(hours=1))
                print(f"url generated! {url}")

            else:
                print(f"url is cached! {url}")

        invoice["url"] = url
    
    # if use_caching:
    #     # https://firebase.google.com/docs/hosting/manage-cache#set_cache-control
    #     return render_template("list.html", invoices=invoices, headers={"Cache-Control": "public, max-age=300, s-maxage=600"}), 200

    print(f"views is {views}")
    # Populate the template with the invoice data and return the page
    return (
        render_template("list.html", invoices=invoices, views=views, approved=approved),
        200,
    )


# POST to / will note approval of selected invoices
# Approval results in updating DB status and moving PDFs to a different folder
@app.route("/", methods=["POST"])
def approve_selected_invoices():
    # Will be making changes in DB and Cloud Storage, so prepare clients
    # db = firestore.Client()
    # gcs = storage.Client()
    bucket = gcs.get_bucket(BUCKET_NAME)

    # Checked boxes will show up as keys in the Flask request form object
    for blob_name in request.form.keys():
        # Set the state to Approved in Firestore
        docref = db.collection("invoices").document(blob_name)
        info = docref.get().to_dict()
        info["state"] = "Approved"
        docref.set(info)

        # Rename storage blob from PROCESSED_PREFIX to APPROVED_PREFIX
        blob = bucket.get_blob(f"{PROCESSED_PREFIX}{blob_name}")
        bucket.rename_blob(blob, f"{APPROVED_PREFIX}{blob_name}")
        cache.incr("reviewer.approve", 1)

    # Show the home page again to users
    return redirect("/")


@app.route("/redis/purge", methods=["GET"])
def redis_purge():
    print("flushdb")
    cache.flushdb()
    return "OK"


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8080, debug=True)

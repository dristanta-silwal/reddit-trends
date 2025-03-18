from flask import Blueprint, render_template, session, redirect, request, jsonify, url_for
import requests
import sqlite3
import datetime
from flask_mail import Mail, Message
import os


jobs_bp = Blueprint("jobs", __name__)
sender_me = os.getenv("MAIL_USERNAME")


def get_job_db_connection():
    conn = sqlite3.connect("jobs.db")
    conn.row_factory = sqlite3.Row
    return conn


def create_job_table():
    conn = get_job_db_connection()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS job_posts (
            id TEXT PRIMARY KEY,
            title TEXT,
            subreddit TEXT,
            upvotes INTEGER,
            comments INTEGER,
            permalink TEXT,
            timestamp TEXT
        )"""
    )
    conn.commit()
    conn.close()
create_job_table()


@jobs_bp.route("/fetch_jobs")
def fetch_jobs():
    access_token = session.get("access_token")
    if not access_token:
        return redirect("/login")
    job_subreddits = ["forhire", "jobbit", "workonline", "slavelabour"]
    all_jobs = []
    conn = get_job_db_connection()
    cursor = conn.cursor()
    for subreddit in job_subreddits:
        url = f"https://oauth.reddit.com/r/{subreddit}/hot"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "RedditJobFinder/0.1"
        }
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            posts = response.json()["data"]["children"]
            for post in posts[:5]:  # Get top 10 job listings per subreddit
                job = {
                    "id": post["data"]["id"],
                    "title": post["data"]["title"],
                    "subreddit": subreddit,
                    "upvotes": post["data"]["ups"],
                    "comments": post["data"]["num_comments"],
                    "permalink": f"https://reddit.com{post['data']['permalink']}",
                    "timestamp": datetime.datetime.utcnow().isoformat()
                }
                all_jobs.append(job)
                cursor.execute(
                    """INSERT OR IGNORE INTO job_posts (id, title, subreddit, upvotes, comments, permalink, timestamp)
                    VALUES (:id, :title, :subreddit, :upvotes, :comments, :permalink, :timestamp)""",
                    job
                )
    conn.commit()
    conn.close()
    return redirect(url_for("jobs.job_listings"))


@jobs_bp.route("/jobs")
def job_listings():
    conn = get_job_db_connection()
    jobs = conn.execute("SELECT * FROM job_posts ORDER BY timestamp DESC").fetchall()
    conn.close()
    return render_template("jobs.html", jobs=jobs)


@jobs_bp.route("/jobs/send_alerts", methods=["POST"])
def send_job_alerts():
    access_token = session.get("access_token")
    if not access_token:
        return redirect("/login")
    recipient_emails = request.form.get("emails", "").split(",")
    recipient_emails = [email.strip() for email in recipient_emails if email.strip()]
    if not recipient_emails:
        return "Please enter at least one valid email address.", 400
    conn = get_job_db_connection()
    jobs = conn.execute("SELECT * FROM job_posts ORDER BY timestamp DESC LIMIT 5").fetchall()
    conn.close()
    if jobs:
        send_email_alert(jobs, recipient_emails)
    return render_template("send_emails.html", posts=jobs, message="Latest job listings sent via email!")


def send_email_alert(posts, recipients):
    from server import mail
    with mail.app.app_context():
        msg = Message(
            f"[{datetime.datetime.today().strftime('%B %d, %Y')}] New Reddit Job Listings!", 
            sender=sender_me, 
            recipients=recipients
        )
        msg.body = "Here are the latest job listings from Reddit:\n\n"
        for post in posts:
            msg.body += f"💼 {post['title']} ({post['upvotes']} upvotes, {post['comments']} comments)\n🔗 {post['permalink']}\n\n"
        mail.send(msg)
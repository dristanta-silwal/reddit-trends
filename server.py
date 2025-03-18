from flask import Flask, jsonify, session, redirect, request, render_template, url_for
import requests
import sqlite3
import datetime
from flask_mail import Mail, Message
from functools import wraps
from dotenv import load_dotenv
import os

from jobs import jobs_bp


load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")
sender_me = os.getenv("MAIL_USERNAME")

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
AUTH_URL = "https://www.reddit.com/api/v1/authorize"
TOKEN_URL = "https://www.reddit.com/api/v1/access_token"

app.config["MAIL_SERVER"] = os.getenv("MAIL_SERVER")
app.config["MAIL_PORT"] = int(os.getenv("MAIL_PORT"))
app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME")
app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD")
app.config["MAIL_USE_TLS"] = os.getenv("MAIL_USE_TLS") == "True"
app.config["MAIL_USE_SSL"] = os.getenv("MAIL_USE_SSL") == "True"

mail = Mail(app)


def get_db_connection():
    conn = sqlite3.connect("trending_posts.db")
    conn.row_factory = sqlite3.Row
    return conn

def create_table():
    conn = get_db_connection()
    cursor = conn.cursor()
    conn.execute(
        """CREATE TABLE IF NOT EXISTS posts (
            id TEXT PRIMARY KEY,
            title TEXT,
            subreddit TEXT,
            upvotes INTEGER,
            comments INTEGER,
            permalink TEXT,
            timestamp TEXT
        )"""
    )
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subreddits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE
        )
    """)
    conn.commit()
    conn.close()

create_table()


SCOPES = "identity read"


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/login")
def login():
    auth_url = f"{AUTH_URL}?client_id={CLIENT_ID}&response_type=code&state=random123&redirect_uri={REDIRECT_URI}&duration=permanent&scope={SCOPES}"
    return redirect(auth_url)


@app.route("/callback")
def callback():
    if "error" in request.args:
        return f"Error: {request.args['error']}"
    auth_code = request.args.get("code")
    data = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": REDIRECT_URI
    }
    auth = (CLIENT_ID, CLIENT_SECRET)
    headers = {"User-Agent": "MyRedditApp/0.1"}

    response = requests.post(TOKEN_URL, data=data, auth=auth, headers=headers)
    token_response = response.json()
    if "access_token" in token_response:
        session["access_token"] = token_response["access_token"]
        session["refresh_token"] = token_response["refresh_token"]
        return redirect("/dashboard")
    else:
        return jsonify(token_response)


@app.route("/search_subreddits", methods=["GET"])
def search_subreddits():
    access_token = session.get("access_token")
    if not access_token:
        return jsonify({"error": "User not logged in"}), 401

    query = request.args.get("query", "").strip()
    if not query:
        return jsonify({"error": "No search term provided"}), 400

    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": "RedditTrendsApp/0.1"
    }

    response = requests.get(f"https://oauth.reddit.com/subreddits/search?q={query}&limit=5", headers=headers)
    data = response.json()
    if "data" in data:
        subreddits = [{"name": sub["data"]["display_name"], "title": sub["data"]["title"]} for sub in data["data"]["children"]]
        return jsonify(subreddits)
    else:
        return jsonify({"error": "No results found"}), 404


@app.route("/add_subreddit", methods=["POST"])
def add_subreddit():
    data = request.json
    subreddit = data.get("subreddit", "").strip()
    if not subreddit:
        return jsonify({"message": "Invalid subreddit name!"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO subreddits (name) VALUES (?)", (subreddit,))
    conn.commit()
    conn.close()

    return jsonify({"message": f"Subreddit {subreddit} added successfully!"}), 200


@app.route("/remove_subreddit", methods=["POST"])
def remove_subreddit():
    data = request.json
    subreddit = data.get("subreddit", "").strip()
    if not subreddit:
        return jsonify({"message": "Invalid subreddit name!"}), 400
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM subreddits WHERE name = ?", (subreddit,))
    conn.commit()
    conn.close()
    return jsonify({"message": f"Subreddit r/{subreddit} removed successfully!"}), 200


@app.route("/dashboard")
def dashboard():
    access_token = session.get("access_token")
    if not access_token:
        return redirect("/login")
    
    conn = get_db_connection()
    subreddits = [row["name"] for row in conn.execute("SELECT name FROM subreddits").fetchall()]
    conn.close()
    
    if not subreddits:
        return render_template("dashboard.html", posts=[], message="No subreddits added. Search and add some!")
    
    trending_posts = []
    for subreddit in subreddits:
        url = f"https://oauth.reddit.com/r/{subreddit}/hot"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "RedditTrendsApp/0.1"
        }
        
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            posts = response.json()["data"]["children"]
            for post in posts[:5]:
                trending_posts.append({
                    "id": post["data"]["id"],
                    "title": post["data"]["title"],
                    "subreddit": subreddit,
                    "upvotes": post["data"]["ups"],
                    "comments": post["data"]["num_comments"],
                    "permalink": f"https://reddit.com{post['data']['permalink']}"
                })
    return render_template("dashboard.html", posts=trending_posts, user_subreddits=subreddits)


@app.route("/dashboard/send_alerts", methods=["GET", "POST"])
def send_alerts():
    access_token = session.get("access_token")
    if not access_token:
        return redirect("/login")

    if request.method == "POST":
        email_input = request.form.get("emails")
        recipient_emails = [email.strip() for email in email_input.split(",") if email.strip()]
        
        if not recipient_emails:
            return "Please enter at least one valid email address.", 400
    
    else:
        recipient_emails = ["dristantasilwal003@gmail.com"]

    conn = get_db_connection()
    subreddits = [row["name"] for row in conn.execute("SELECT name FROM subreddits").fetchall()]
    conn.close()
    
    trending_posts = []
    for subreddit in subreddits:
        url = f"https://oauth.reddit.com/r/{subreddit}/hot"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "RedditTrendsApp/0.1"
        }
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            posts = response.json()["data"]["children"]
            for post in posts[:5]:
                trending_posts.append({
                    "title": post["data"]["title"],
                    "subreddit": subreddit,
                    "upvotes": post["data"]["ups"],
                    "comments": post["data"]["num_comments"],
                    "permalink": f"https://reddit.com{post['data']['permalink']}"
                })
    
    if trending_posts:
        send_email_alert(trending_posts, recipient_emails)
    
    message = "Trending Reddit posts have been sent via email!" if trending_posts else "No trending posts found!"
    return render_template("send_emails.html", posts=trending_posts, message=message)


def send_email_alert(posts, recipients):
    with app.app_context():
        msg = Message(
            f"[{datetime.datetime.today().strftime('%B %d, %Y')}] Trending Reddit Posts!", 
            sender=sender_me, 
            recipients=recipients
        )
        msg.body = "Here are the top trending Reddit posts:\n\n"
        for post in posts:
            msg.body += f"📌 {post['title']} ({post['upvotes']} upvotes, {post['comments']} comments)\n🔗 {post['permalink']}\n\n"
        mail.send(msg)


app.register_blueprint(jobs_bp)


if __name__ == "__main__":
    app.run(debug=True)
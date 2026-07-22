import os
import json
import requests

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SEEN_JOBS_FILE = "seen_jobs.json"

# Keywords you are targeting for SDE-2
KEYWORDS = ["java", "spring", "backend", "sde 2", "sde-2", "sde ii", "software engineer 2"]

# Company ATS Boards (Add any company hosted on Lever or Greenhouse)
GREENHOUSE_COMPANIES = ["blinkit", "swiggy", "razorpay", "cred", "atlassian"]
LEVER_COMPANIES = ["flipkart", "zepto", "groww"]

def load_seen_jobs():
    if os.path.exists(SEEN_JOBS_FILE):
        with open(SEEN_JOBS_FILE, "r") as f:
            try:
                return set(json.load(f))
            except json.JSONDecodeError:
                return set()
    return set()

def save_seen_jobs(seen_jobs):
    with open(SEEN_JOBS_FILE, "w") as f:
        json.dump(list(seen_jobs), f, indent=2)

def send_telegram_alert(job_title, company, job_url):
    message = (
        f"🚨 *NEW JOB OPENING DETECTED!*\n\n"
        f"🏢 *Company:* {company.upper()}\n"
        f"💼 *Role:* {job_title}\n"
        f"🔗 [Apply Immediately]({job_url})"
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    requests.post(url, json=payload)

def fetch_greenhouse_jobs():
    new_jobs = []
    for company in GREENHOUSE_COMPANIES:
        url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs"
        try:
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                data = res.json()
                for job in data.get("jobs", []):
                    title = job.get("title", "")
                    job_id = f"{company}_{job.get('id')}"
                    job_url = job.get("absolute_url", "")
                    
                    if any(kw in title.lower() for kw in KEYWORDS):
                        new_jobs.append((job_id, title, company, job_url))
        except Exception as e:
            print(f"Error fetching Greenhouse for {company}: {e}")
    return new_jobs

def fetch_lever_jobs():
    new_jobs = []
    for company in LEVER_COMPANIES:
        url = f"https://api.lever.co/v0/postings/{company}"
        try:
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                data = res.json()
                for job in data:
                    title = job.get("text", "")
                    job_id = f"{company}_{job.get('id')}"
                    job_url = job.get("hostedUrl", "")
                    
                    if any(kw in title.lower() for kw in KEYWORDS):
                        new_jobs.append((job_id, title, company, job_url))
        except Exception as e:
            print(f"Error fetching Lever for {company}: {e}")
    return new_jobs

def main():
    seen_jobs = load_seen_jobs()
    all_fetched_jobs = fetch_greenhouse_jobs() + fetch_lever_jobs()
    
    new_alert_count = 0
    for job_id, title, company, job_url in all_fetched_jobs:
        if job_id not in seen_jobs:
            send_telegram_alert(title, company, job_url)
            seen_jobs.add(job_id)
            new_alert_count += 1
            
    save_seen_jobs(seen_jobs)
    print(f"Check complete. Triggered {new_alert_count} new job alerts.")

if __name__ == "__main__":
    main()

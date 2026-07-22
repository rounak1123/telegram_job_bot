import os
import json
import time
import requests

# Sanitize environment variables to strip leading/trailing spaces or unwanted 'bot' prefixes
raw_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
if raw_token.lower().startswith("bot"):
    raw_token = raw_token[3:]  # Strip 'bot' prefix if accidentally included in GitHub secret

TELEGRAM_BOT_TOKEN = raw_token
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
SEEN_JOBS_FILE = "seen_jobs.json"

ROLE_KEYWORDS = [
    "java", "spring", "backend", "sde 2", "sde-2", "sde ii", 
    "software engineer 2", "software development engineer ii",
    "microservices", "distributed", "platform", "senior engineer"
]

LOCATION_KEYWORDS = [
    "bengaluru", "bangalore", "karnataka", "india", "remote"
]

GREENHOUSE_COMPANIES = [
    "razorpay", "atlassian", "cred", "swiggy", "sharechat", 
    "phonepe", "commvault", "arcesium", "blinkit", "zomato", 
    "coupang", "intuit", "rubrik"
]

LEVER_COMPANIES = [
    "zepto", "uber", "flipkart", "deshaw", "myntra"
]

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

def is_valid_location(location_str):
    loc = (location_str or "").lower()
    return any(lk in loc for lk in LOCATION_KEYWORDS)

def send_telegram_alert(job_title, company, job_url, location):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("CRITICAL ERROR: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing from GitHub Secrets!")
        return

    clean_title = str(job_title).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    clean_company = str(company).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    clean_location = str(location).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    message = (
        f"🚨 <b>NEW BENGALURU / INDIA JOB DETECTED!</b>\n\n"
        f"🏢 <b>Company:</b> {clean_company.upper()}\n"
        f"💼 <b>Role:</b> {clean_title}\n"
        f"📍 <b>Location:</b> {clean_location}\n\n"
        f'🔗 <a href="{job_url}">Apply Immediately</a>'
    )
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code != 200:
            print(f"Telegram API Error ({res.status_code}): {res.text}")
    except Exception as e:
        print(f"Error sending Telegram alert: {e}")
        
    time.sleep(0.5)

def fetch_greenhouse_jobs():
    new_jobs = []
    headers = {"User-Agent": "Mozilla/5.0"}
    for company in GREENHOUSE_COMPANIES:
        url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs"
        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                for job in data.get("jobs", []):
                    title = job.get("title", "")
                    job_id = f"{company}_{job.get('id')}"
                    job_url = job.get("absolute_url", "")
                    location = job.get("location", {}).get("name", "Bengaluru, India")
                    
                    if any(kw in title.lower() for kw in ROLE_KEYWORDS) and is_valid_location(location):
                        new_jobs.append((job_id, title, company, job_url, location))
        except Exception as e:
            print(f"Error fetching Greenhouse ({company}): {e}")
    return new_jobs

def fetch_lever_jobs():
    new_jobs = []
    headers = {"User-Agent": "Mozilla/5.0"}
    for company in LEVER_COMPANIES:
        url = f"https://api.lever.co/v0/postings/{company}"
        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                for job in data:
                    title = job.get("text", "")
                    job_id = f"{company}_{job.get('id')}"
                    job_url = job.get("hostedUrl", "")
                    location = job.get("categories", {}).get("location", "Bengaluru, India")
                    
                    if any(kw in title.lower() for kw in ROLE_KEYWORDS) and is_valid_location(location):
                        new_jobs.append((job_id, title, company, job_url, location))
        except Exception as e:
            print(f"Error fetching Lever ({company}): {e}")
    return new_jobs

def fetch_amazon_jobs():
    new_jobs = []
    url = "https://www.amazon.jobs/en/search.json?base_query=software%20development%20engineer&loc_query=Bangalore%2C%20Karnataka%2C%20India&result_limit=30&sort=recent"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            for job in data.get("jobs", []):
                title = job.get("title", "")
                job_id = f"amazon_{job.get('id_icims')}"
                job_url = f"https://www.amazon.jobs{job.get('job_path')}"
                location = job.get("location", "Bengaluru, India")
                
                if any(kw in title.lower() for kw in ROLE_KEYWORDS) and is_valid_location(location):
                    new_jobs.append((job_id, title, "Amazon", job_url, location))
    except Exception as e:
        print(f"Error fetching Amazon: {e}")
    return new_jobs

def fetch_microsoft_jobs():
    new_jobs = []
    url = "https://services.careers.microsoft.com/api/v1/search?lc=Bengaluru,%20Karnataka,%20India&p=Software%20Engineering&l=en_us&pg=1&pgSz=20&o=Recent"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            operation_result = data.get("operationResult", {})
            result = operation_result.get("result", {})
            jobs = result.get("jobs", [])
            for job in jobs:
                title = job.get("title", "")
                job_id = f"microsoft_{job.get('jobId')}"
                job_url = f"https://jobs.careers.microsoft.com/global/en/job/{job.get('jobId')}"
                location = job.get("properties", {}).get("primaryLocation", "Bengaluru, India")
                
                if any(kw in title.lower() for kw in ROLE_KEYWORDS) and is_valid_location(location):
                    new_jobs.append((job_id, title, "Microsoft", job_url, location))
    except Exception as e:
        print(f"Error fetching Microsoft: {e}")
    return new_jobs

def fetch_enterprise_jobs():
    new_jobs = []
    headers = {"User-Agent": "Mozilla/5.0"}
    enterprise_boards = [
        {"company": "Salesforce", "url": "https://salesforce.eightfold.ai/api/apply/v2/jobs?domain=salesforce.com&location=Bengaluru&num=20"},
        {"company": "Goldman Sachs", "url": "https://goldmansachs.eightfold.ai/api/apply/v2/jobs?domain=goldmansachs.com&location=Bengaluru&num=20"},
        {"company": "Walmart", "url": "https://walmart.eightfold.ai/api/apply/v2/jobs?domain=walmart.com&location=Bengaluru&num=20"},
        {"company": "Adobe", "url": "https://adobe.eightfold.ai/api/apply/v2/jobs?domain=adobe.com&location=Bengaluru&num=20"}
    ]
    
    for board in enterprise_boards:
        company = board["company"]
        try:
            res = requests.get(board["url"], headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                for pos in data.get("positions", []):
                    title = pos.get("name", "")
                    job_id = f"{company.lower()}_{pos.get('id')}"
                    job_url = pos.get("canonicalPositionUrl", f"https://{company.lower()}.com/careers")
                    location = pos.get("location", "Bengaluru, India")
                    
                    if any(kw in title.lower() for kw in ROLE_KEYWORDS) and is_valid_location(location):
                        new_jobs.append((job_id, title, company, job_url, location))
        except Exception as e:
            print(f"Error fetching Enterprise API ({company}): {e}")
            
    return new_jobs

def main():
    seen_jobs = load_seen_jobs()
    
    all_jobs = (
        fetch_greenhouse_jobs() + 
        fetch_lever_jobs() + 
        fetch_amazon_jobs() + 
        fetch_microsoft_jobs() + 
        fetch_enterprise_jobs()
    )
    
    print(f"Found {len(all_jobs)} matching SDE-2/Backend roles in Bengaluru/India.")
    
    new_alert_count = 0
    for job_id, title, company, job_url, location in all_jobs:
        if job_id not in seen_jobs:
            send_telegram_alert(title, company, job_url, location)
            seen_jobs.add(job_id)
            new_alert_count += 1
            
    save_seen_jobs(seen_jobs)
    print(f"Execution complete. Sent {new_alert_count} alerts.")

if __name__ == "__main__":
    main()

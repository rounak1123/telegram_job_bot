import os
import json
import requests

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SEEN_JOBS_FILE = "seen_jobs.json"

# Target Keywords for SDE-2 Java / Backend roles
KEYWORDS = [
    "java", "spring", "backend", "sde 2", "sde-2", "sde ii", 
    "software engineer 2", "software development engineer ii",
    "microservices", "distributed", "platform", "senior engineer"
]

# ---------------------------------------------------------
# 1. Greenhouse ATS Companies
# ---------------------------------------------------------
GREENHOUSE_COMPANIES = [
    "razorpay", "atlassian", "cred", "swiggy", "sharechat", 
    "phonepe", "commvault", "arcesium", "blinkit", "zomato", 
    "coupang", "intuit", "rubrik"
]

# ---------------------------------------------------------
# 2. Lever ATS Companies
# ---------------------------------------------------------
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

def send_telegram_alert(job_title, company, job_url, location="India"):
    message = (
        f"🚨 *NEW JOB OPENING DETECTED!*\n\n"
        f"🏢 *Company:* {company.upper()}\n"
        f"💼 *Role:* {job_title}\n"
        f"📍 *Location:* {location}\n\n"
        f"🔗 [Apply Immediately]({job_url})"
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error sending Telegram alert: {e}")

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
                    location = job.get("location", {}).get("name", "India")
                    
                    if any(kw in title.lower() for kw in KEYWORDS):
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
                    location = job.get("categories", {}).get("location", "India")
                    
                    if any(kw in title.lower() for kw in KEYWORDS):
                        new_jobs.append((job_id, title, company, job_url, location))
        except Exception as e:
            print(f"Error fetching Lever ({company}): {e}")
    return new_jobs

def fetch_amazon_jobs():
    new_jobs = []
    url = "https://www.amazon.jobs/en/search.json?base_query=software%20development%20engineer&loc_query=India&result_limit=30&sort=recent"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            for job in data.get("jobs", []):
                title = job.get("title", "")
                job_id = f"amazon_{job.get('id_icims')}"
                job_url = f"https://www.amazon.jobs{job.get('job_path')}"
                location = job.get("location", "India")
                
                if any(kw in title.lower() for kw in KEYWORDS):
                    new_jobs.append((job_id, title, "Amazon", job_url, location))
    except Exception as e:
        print(f"Error fetching Amazon: {e}")
    return new_jobs

def fetch_microsoft_jobs():
    new_jobs = []
    url = "https://services.careers.microsoft.com/api/v1/search?lc=India&p=Software%20Engineering&l=en_us&pg=1&pgSz=20&o=Recent"
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
                location = job.get("properties", {}).get("primaryLocation", "India")
                
                if any(kw in title.lower() for kw in KEYWORDS):
                    new_jobs.append((job_id, title, "Microsoft", job_url, location))
    except Exception as e:
        print(f"Error fetching Microsoft: {e}")
    return new_jobs

def fetch_eightfold_and_enterprise_jobs():
    """
    Handles Eightfold/Workday API endpoints for enterprise companies:
    Google, Salesforce, Goldman Sachs, JP Morgan, Wells Fargo, Walmart, Oracle, Adobe
    """
    new_jobs = []
    headers = {"User-Agent": "Mozilla/5.0"}
    
    # Eightfold API endpoints for large enterprises
    enterprise_boards = [
        {"company": "Salesforce", "url": "https://salesforce.eightfold.ai/api/apply/v2/jobs?domain=salesforce.com&location=India&num=20"},
        {"company": "Goldman Sachs", "url": "https://goldmansachs.eightfold.ai/api/apply/v2/jobs?domain=goldmansachs.com&location=India&num=20"},
        {"company": "Walmart", "url": "https://walmart.eightfold.ai/api/apply/v2/jobs?domain=walmart.com&location=India&num=20"},
        {"company": "Adobe", "url": "https://adobe.eightfold.ai/api/apply/v2/jobs?domain=adobe.com&location=India&num=20"}
    ]
    
    for board in enterprise_boards:
        company = board["company"]
        try:
            res = requests.get(board["url"], headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                positions = data.get("positions", [])
                for pos in positions:
                    title = pos.get("name", "")
                    job_id = f"{company.lower()}_{pos.get('id')}"
                    job_url = pos.get("canonicalPositionUrl", f"https://{company.lower()}.com/careers")
                    location = pos.get("location", "India")
                    
                    if any(kw in title.lower() for kw in KEYWORDS):
                        new_jobs.append((job_id, title, company, job_url, location))
        except Exception as e:
            print(f"Error fetching Enterprise API ({company}): {e}")
            
    return new_jobs

def main():
    seen_jobs = load_seen_jobs()
    
    print("Fetching jobs from GreenHouse...")
    greenhouse_jobs = fetch_greenhouse_jobs()
    
    print("Fetching jobs from Lever...")
    lever_jobs = fetch_lever_jobs()
    
    print("Fetching jobs from Amazon...")
    amazon_jobs = fetch_amazon_jobs()
    
    print("Fetching jobs from Microsoft...")
    microsoft_jobs = fetch_microsoft_jobs()
    
    print("Fetching jobs from Enterprise Boards (Salesforce, Goldman Sachs, Walmart, Adobe)...")
    enterprise_jobs = fetch_eightfold_and_enterprise_jobs()
    
    all_jobs = greenhouse_jobs + lever_jobs + amazon_jobs + microsoft_jobs + enterprise_jobs
    print(f"Total matching jobs found across all 27 companies: {len(all_jobs)}")
    
    new_alert_count = 0
    for job_id, title, company, job_url, location in all_jobs:
        if job_id not in seen_jobs:
            send_telegram_alert(title, company, job_url, location)
            seen_jobs.add(job_id)
            new_alert_count += 1
            
    save_seen_jobs(seen_jobs)
    print(f"Check complete. Triggered {new_alert_count} new job alerts.")

if __name__ == "__main__":
    main()

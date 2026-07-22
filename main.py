import os
import json
import time
import re
import requests
from jobspy import scrape_jobs

# Clean environment variables
raw_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
if raw_token.lower().startswith("bot"):
    raw_token = raw_token[3:]

TELEGRAM_BOT_TOKEN = raw_token
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
SEEN_JOBS_FILE = "seen_jobs.json"

# Target Keywords for SDE-1 / SDE-2 / Mid-Level Backend Roles
ROLE_KEYWORDS = [
    "java", "spring", "backend", "sde 2", "sde-2", "sde ii", "sde 1", "sde-1", "sde i",
    "software engineer 2", "software development engineer ii", "software engineer",
    "microservices", "distributed", "platform"
]

# Exclude Titles meant for 6+ YOE / Senior Leadership
EXCLUDE_TITLE_KEYWORDS = [
    "sde 3", "sde-3", "sde iii", "sde3", "sde 4", "sde-4", "sde iv", "sde4",
    "staff", "principal", "director", "head of", "lead engineer", "tech lead",
    "engineering manager", "manager", "architect", "senior manager", "sr manager"
]

# Location Keywords
LOCATION_KEYWORDS = [
    "bengaluru", "bangalore", "karnataka", "india", "remote"
]

GREENHOUSE_COMPANIES = [
    "razorpay", "atlassian", "cred", "swiggy", "sharechat", 
    "phonepe", "commvault", "arcesium", "blinkit", "zomato", 
    "coupang", "intuit", "rubrik", "inmobi", "meesho", "dream11",
    "paytm", "urbancompany", "coindcx", "druva"
]

LEVER_COMPANIES = [
    "zepto", "uber", "flipkart", "deshaw", "myntra", "groww", "postman"
]

ASHBY_COMPANIES = [
    "rippling", "notion", "figma", "ramp", "airtable"
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

def is_valid_experience(title, description=""):
    """
    Returns True ONLY if experience required is 5 years or less.
    Filters out roles explicitly requiring >5 YOE or SDE-3+ titles.
    """
    title_lower = title.lower()
    text_combined = f"{title} {description}".lower()

    # 1. Check title exclusions
    for ex in EXCLUDE_TITLE_KEYWORDS:
        if ex in title_lower:
            return False

    # 2. Regex pattern to catch experience mentions like "6+ years", "7-10 yrs", "8+ yoe"
    experience_patterns = [
        r'(\d+)\+?\s*(?:-\s*\d+\s*)?(?:years|yrs|yoe)',
        r'(?:minimum|at least|req|requires)\s*(\d+)\+?\s*(?:years|yrs|yoe)'
    ]

    for pattern in experience_patterns:
        matches = re.findall(pattern, text_combined)
        for m in matches:
            try:
                min_years = int(m)
                # If required years is strictly greater than 5, reject
                if min_years > 5:
                    return False
            except ValueError:
                pass

    return True

def send_telegram_alert(job_title, company, job_url, location, source="Career Site"):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("CRITICAL ERROR: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing!")
        return

    clean_title = str(job_title).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    clean_company = str(company).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    clean_location = str(location).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    message = (
        f"🚨 <b>NEW SDE-2 JOB DETECTED (≤ 5 YOE)!</b>\n\n"
        f"🏢 <b>Company:</b> {clean_company.upper()}\n"
        f"💼 <b>Role:</b> {clean_title}\n"
        f"📍 <b>Location:</b> {clean_location}\n"
        f"🌐 <b>Source:</b> {source}\n\n"
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
        print(f"Error sending alert: {e}")
        
    time.sleep(0.5)

# ---------------------------------------------------------
# Scraper Engines
# ---------------------------------------------------------

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
                    
                    if (any(kw in title.lower() for kw in ROLE_KEYWORDS) and 
                        is_valid_location(location) and 
                        is_valid_experience(title)):
                        new_jobs.append((job_id, title, company, job_url, location, "Greenhouse"))
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
                    description = job.get("descriptionPlain", "")
                    
                    if (any(kw in title.lower() for kw in ROLE_KEYWORDS) and 
                        is_valid_location(location) and 
                        is_valid_experience(title, description)):
                        new_jobs.append((job_id, title, company, job_url, location, "Lever"))
        except Exception as e:
            print(f"Error fetching Lever ({company}): {e}")
    return new_jobs

def fetch_ashby_jobs():
    new_jobs = []
    headers = {"User-Agent": "Mozilla/5.0"}
    for company in ASHBY_COMPANIES:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{company}"
        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                for job in data.get("jobs", []):
                    title = job.get("title", "")
                    job_id = f"ashby_{company}_{job.get('id')}"
                    job_url = job.get("jobUrl", "")
                    location = job.get("location", "Bengaluru, India")
                    
                    if (any(kw in title.lower() for kw in ROLE_KEYWORDS) and 
                        is_valid_location(location) and 
                        is_valid_experience(title)):
                        new_jobs.append((job_id, title, company, job_url, location, "Ashby"))
        except Exception as e:
            print(f"Error fetching Ashby ({company}): {e}")
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
                description = job.get("description", "")
                
                if (any(kw in title.lower() for kw in ROLE_KEYWORDS) and 
                    is_valid_location(location) and 
                    is_valid_experience(title, description)):
                    new_jobs.append((job_id, title, "Amazon", job_url, location, "Amazon Careers"))
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
            jobs = data.get("operationResult", {}).get("result", {}).get("jobs", [])
            for job in jobs:
                title = job.get("title", "")
                job_id = f"microsoft_{job.get('jobId')}"
                job_url = f"https://jobs.careers.microsoft.com/global/en/job/{job.get('jobId')}"
                location = job.get("properties", {}).get("primaryLocation", "Bengaluru, India")
                
                if (any(kw in title.lower() for kw in ROLE_KEYWORDS) and 
                    is_valid_location(location) and 
                    is_valid_experience(title)):
                    new_jobs.append((job_id, title, "Microsoft", job_url, location, "Microsoft Careers"))
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
                    
                    if (any(kw in title.lower() for kw in ROLE_KEYWORDS) and 
                        is_valid_location(location) and 
                        is_valid_experience(title)):
                        new_jobs.append((job_id, title, company, job_url, location, f"{company} Careers"))
        except Exception as e:
            print(f"Error fetching Enterprise API ({company}): {e}")
            
    return new_jobs

def fetch_linkedin_and_indeed_jobs():
    new_jobs = []
    print("Scraping LinkedIn & Indeed via JobSpy...")
    try:
        jobs = scrape_jobs(
            site_name=["linkedin", "indeed"],
            search_term="SDE 2 Java Spring Boot",
            location="Bengaluru, Karnataka, India",
            results_wanted=15,
            hours_old=12,
            country_indeed='India'
        )
        if jobs is not None and not jobs.empty:
            for _, row in jobs.iterrows():
                title = str(row.get('title', ''))
                company = str(row.get('company', 'Unknown'))
                job_url = str(row.get('job_url', ''))
                job_id = f"jobspy_{row.get('id', hash(job_url))}"
                location = str(row.get('location', 'Bengaluru, India'))
                site = str(row.get('site', 'LinkedIn/Indeed')).title()
                description = str(row.get('description', ''))

                if (any(kw in title.lower() for kw in ROLE_KEYWORDS) and 
                    is_valid_location(location) and 
                    is_valid_experience(title, description)):
                    new_jobs.append((job_id, title, company, job_url, location, site))
    except Exception as e:
        print(f"Error scraping LinkedIn/Indeed: {e}")
    return new_jobs

def main():
    seen_jobs = load_seen_jobs()
    
    all_jobs = (
        fetch_greenhouse_jobs() + 
        fetch_lever_jobs() + 
        fetch_ashby_jobs() +
        fetch_amazon_jobs() + 
        fetch_microsoft_jobs() + 
        fetch_enterprise_jobs() +
        fetch_linkedin_and_indeed_jobs()
    )
    
    print(f"Found {len(all_jobs)} matching SDE-2/Backend roles (<= 5 YOE) in Bengaluru.")
    
    new_alert_count = 0
    for job_id, title, company, job_url, location, source in all_jobs:
        if job_id not in seen_jobs:
            send_telegram_alert(title, company, job_url, location, source)
            seen_jobs.add(job_id)
            new_alert_count += 1
            
    save_seen_jobs(seen_jobs)
    print(f"Execution complete. Sent {new_alert_count} alerts.")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Job tracker for Bengaluru backend / SDE-2 roles -> Telegram.

Coverage model
  1. DIRECT   : company career API (Greenhouse, Lever, Ashby, SmartRecruiters, Workday,
                Oracle HCM, Eightfold + custom endpoints for Amazon, Atlassian, Microsoft,
                Uber, Goldman, Google). Each company lists candidate sources; the first one
                that works is verified and cached in the state file.
  2. FALLBACK : companies with no working direct source are searched through JobSpy
                (LinkedIn / Indeed / Naukri / Google), a few per run, round-robin.

    python main.py --check        # test every source, print a report, send nothing
    python main.py --check-agg    # same, but also exercise the JobSpy fallback
    python main.py                # normal run
"""
import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests

# =============================================================== settings
raw_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
if raw_token.lower().startswith("bot"):
    raw_token = raw_token[3:]
TELEGRAM_BOT_TOKEN = raw_token
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

STATE_FILE = "seen_jobs.json"
TIMEOUT = 25
WORKERS = 8
DETAIL_CAP = 150          # max job-detail fetches per run (rest are retried next run)
AGG_BUDGET = int(os.getenv("AGG_BUDGET", "6"))   # JobSpy queries per run
DISABLE_AGG = os.getenv("DISABLE_AGGREGATOR") == "1"
DIGEST_THRESHOLD = 10     # more alerts than this in one run -> grouped digest messages
FAIL_ALERT_AFTER = 6      # consecutive failed runs of a working source before a warning
HEARTBEAT_HOURS = 24

# ---- experience filter (you: ~3.5 YOE) ----
MAX_MIN_YOE = 5           # a job's MINIMUM required experience must be <= this
MAX_MIN_YOE_SENIOR = 4    # stricter for titles with senior/sr/lead
MIN_MAX_YOE = 3           # skip bands whose UPPER bound is below this ("0-2 yrs")

# ---- location ----
LOCATION_KEYWORDS = ["bengaluru", "bangalore", "karnataka"]

# ---- title filters (whole-word, after normalising: "SDE-II" -> "sde 2") ----
INCLUDE = [
    "java", "spring", "backend", "back end", "microservices", "distributed systems",
    "sde 2", "software engineer 2", "software development engineer 2",
    "engineer 2", "developer 2", "mts", "mts 2",
    # "sde 1", "software engineer 1",       # uncomment to accept down-levels
]
GENERIC_TITLE = re.compile(
    r"\b(software|backend|back end|platform|application|full stack|fullstack|java)\b.*\b(engineer|developer)\b"
    r"|software development engineer|\bsde\b|\bmts\b")
EXCLUDE = [  # hard rejects
    "frontend", "front end", "react", "angular", "vue", "ui", "ux", "android", "ios", "mobile",
    "qa", "sdet", "test", "testing", "devops", "sre", "infrastructure", "cloud engineer",
    "data engineer", "data scientist", "machine learning", "ml", "embedded", "firmware", "hardware",
    "security", "network", "support", "solutions", "sales", "customer", "analyst", "scientist",
    "designer", "consultant", "technician", "recruiter", "specialist", "writer", "salesforce",
    "intern", "internship", "fresher", "trainee", "graduate",
    "sde 3", "sde 4", "engineer 3", "engineer 4", "developer 3",
    "staff", "principal", "director", "head of", "vice president", "vp",
    "manager", "architect",
]
SOFT_SENIOR = ["senior", "sr", "lead"]   # allowed only if the JD/title states experience <= 4 yrs

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# ======================================================== company registry
# C(name, est. total comp, difficulty/10, Java-Spring fit, *candidate sources)
# candidate source formats:
#   ("greenhouse", slug) ("lever", slug) ("ashby", slug) ("smartrecruiters", Slug)
#   ("workday", tenant, wdN, site) ("oracle", host, siteNumber) ("eightfold", host, domain)
#   ("amazon",) ("atlassian",) ("microsoft",) ("uber",) ("goldman",) ("google",)
# No source / all dead  ->  covered by the JobSpy fallback automatically.
COMPANIES = []


def C(name, comp, diff, fit, *src, alias=None):
    COMPANIES.append({"name": name, "comp": comp, "diff": diff, "fit": fit,
                      "src": [tuple(s) for s in src], "alias": alias or name})


def GH(*slugs): return [("greenhouse", s) for s in slugs]
def LV(*slugs): return [("lever", s) for s in slugs]
def AB(*slugs): return [("ashby", s) for s in slugs]
def ALL(*slugs): return GH(*slugs) + LV(*slugs) + AB(*slugs)


# ---- High Java/Spring fit
C("Atlassian", "55-85L", 9, "High", ("atlassian",))
C("Uber", "60-90L", 9, "High", ("uber",))
C("Amazon", "45-65L", 8, "High", ("amazon",))
C("Flipkart", "40-65L", 7.5, "High", *ALL("flipkart"))
C("PhonePe", "40-65L", 7.5, "High", *GH("phonepe"))
C("Goldman Sachs", "40-60L", 7.5, "High", ("goldman",))
C("Morgan Stanley", "40-60L", 7, "High", ("eightfold", "morganstanley.eightfold.ai", "morganstanley.com"))
C("Intuit", "40-60L", 7, "High", *GH("intuit"))
C("Adobe", "40-60L", 7, "High", ("workday", "adobe", "wd5", "external_experienced"))
C("Groww", "38-60L", 7, "High", *ALL("groww"))
C("Meesho", "38-60L", 7, "High", *ALL("meesho"))
C("Swiggy", "38-60L", 7, "High", *ALL("swiggy"))
C("Walmart Global Tech", "40-60L", 6.5, "High", ("workday", "walmart", "wd5", "WalmartExternal"), alias="Walmart")
C("Salesforce", "35-55L", 6.5, "High", ("workday", "salesforce", "wd12", "External_Career_Site"))
C("Oracle", "35-55L", 6.5, "High", ("oracle", "eeho.fa.us2.oraclecloud.com", "CX_45001"))
C("Dream11", "35-55L", 6, "High", *ALL("dreamsports", "dream11"), alias="Dream Sports")
C("Navi", "35-55L", 6, "High", *ALL("navi"))
C("Zeta", "35-55L", 6, "High", *ALL("zeta", "zetatech"))
C("ServiceNow", "35-50L", 6, "High")
C("Workday", "35-50L", 6, "High", ("workday", "workday", "wd5", "Workday"))
C("SAP Labs", "35-50L", 5.5, "High", alias="SAP")
C("PayPal", "35-50L", 5.5, "High", ("workday", "paypal", "wd1", "jobs"))
C("Visa", "35-50L", 5.5, "High", ("smartrecruiters", "Visa"))
C("Mastercard", "35-50L", 5.5, "High", ("workday", "mastercard", "wd1", "CorporateCareers"))
C("American Express", "35-50L", 5.5, "High", ("eightfold", "aexp.eightfold.ai", "aexp.com"), alias="Amex")
C("Booking.com", "35-50L", 5.5, "High", alias="Booking")
C("Expedia", "35-50L", 5.5, "High", alias="Expedia Group")
C("Target", "35-50L", 5, "High", ("workday", "target", "wd5", "targetcareers"))
C("Lowe's", "35-50L", 5, "High", ("workday", "lowes", "wd5", "LWS_External_CS"))
C("JPMorgan Chase", "32-45L", 5, "High", ("oracle", "jpmc.fa.oraclecloud.com", "CX_1001"), alias="JPMorgan")
C("Barclays", "32-45L", 5, "High")
C("Wells Fargo", "32-45L", 4.5, "High", ("workday", "wf", "wd1", "WellsFargoJobs"))
C("Citi", "32-45L", 4.5, "High", alias="Citigroup")
C("Sprinklr", "30-45L", 4.5, "High", *ALL("sprinklr"))
# ---- Medium fit
C("Google", "50-80L", 9.5, "Medium", ("google",))
C("Databricks", "55-90L", 9, "Medium", *GH("databricks"))
C("Stripe", "55-90L", 9, "Medium", *GH("stripe"))
C("Apple", "55-90L", 8.5, "Medium")
C("Cred", "40-65L", 8, "Medium", *ALL("cred"))
C("Microsoft", "40-60L", 7.5, "Medium", ("microsoft",))
C("Zepto", "38-60L", 7, "Medium", *ALL("zepto"))
C("Zomato", "38-60L", 7, "Medium", *ALL("zomato", "eternal"), alias="Eternal")
C("VMware/Broadcom", "35-55L", 6, "Medium", ("workday", "broadcom", "wd1", "External_Career"), alias="Broadcom")
C("Slice", "35-55L", 6, "Medium", *ALL("sliceit", "slice"))
C("Cisco", "35-50L", 5.5, "Medium")
C("Freshworks", "30-45L", 4.5, "Medium", *ALL("freshworks"))
C("Chargebee", "30-45L", 4.5, "Medium", *ALL("chargebee"))
C("Samsung R&D", "30-45L", 4, "Medium", alias="Samsung")
C("Zoho", "30-45L", 4, "Medium")
# ---- Low fit (Go / C++ / Haskell / Node / embedded) and quant
C("Graviton Research", "50L-1.5Cr", 10, "Low", *ALL("gravitonresearchcapital", "graviton"))
C("Tower Research", "50L-1.5Cr", 10, "Low", *GH("towerresearchcapital"))
C("Quadeye", "50L-1.5Cr", 10, "Low", *ALL("quadeye"))
C("Squarepoint Capital", "50L-1.5Cr", 10, "Low", *GH("squarepoint"))
C("D. E. Shaw", "50L-1.5Cr", 10, "Low", *LV("deshaw"), alias="DE Shaw")
C("Optiver", "50L-1.5Cr", 10, "Low", *GH("optiver"))
C("Hudson River Trading", "50L-1.5Cr", 10, "Low", *GH("wehrtyou"), alias="HRT")
C("WorldQuant", "50L-1.5Cr", 10, "Low", *GH("worldquant"))
C("NVIDIA", "55-90L", 8.5, "Low", ("workday", "nvidia", "wd5", "NVIDIAExternalCareerSite"))
C("Rubrik", "55-90L", 8.5, "Low", *GH("rubrik"))
C("Razorpay", "40-65L", 7.5, "Low", *GH("razorpaysoftwareprivatelimited", "razorpay"))
C("Juspay", "38-60L", 7.5, "Low", *ALL("juspay"))
C("Postman", "38-60L", 7, "Low", *GH("postman"))
C("Nutanix", "35-55L", 6.5, "Low", ("workday", "nutanix", "wd1", "Careers"))
C("Cohesity", "35-55L", 6.5, "Low", ("workday", "cohesity", "wd1", "Cohesity_Careers"), *GH("cohesity"))
C("NetApp", "35-55L", 6, "Low", ("workday", "netapp", "wd1", "NetApp-Careers"))
C("Qualcomm", "30-45L", 4.5, "Low", ("workday", "qualcomm", "wd12", "External"))
C("Texas Instruments", "30-45L", 4.5, "Low", ("workday", "ti", "wd5", "External"), alias="TI")
# ---- from your original list (kept so nothing you had is lost)
for _n in ["ShareChat", "Commvault", "Arcesium", "Blinkit", "Coupang", "InMobi", "Paytm",
           "Urban Company", "CoinDCX", "Druva", "Myntra", "Rubrik"]:
    if not any(c["name"] == _n for c in COMPANIES):
        _slug = re.sub(r"[^a-z]", "", _n.lower())
        C(_n, "?", None, "?", *ALL(_slug))

# generic fallback searches (also catch mid-size startups / GCCs not listed above)
AGG_GENERIC = [
    "SDE 2 Java Backend Bengaluru",
    "Software Engineer II Backend Bangalore",
    "SDE 2 Spring Boot Bengaluru",
    "Java Spring Boot microservices developer Bengaluru",
]
AGG_SITES = ["linkedin", "indeed", "naukri", "google"]
META = {c["name"]: c for c in COMPANIES}

# ================================================================ helpers
def slugify(s): return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def normalize_title(title):
    t = (title or "").lower()
    t = re.sub(r"[-_/,()|:–—.]", " ", t)
    t = t.replace("member of technical staff", "mts")
    t = re.sub(r"([a-z])(\d)", r"\1 \2", t)
    t = re.sub(r"\biv\b", "4", t)
    t = re.sub(r"\biii\b", "3", t)
    t = re.sub(r"\bii\b", "2", t)
    t = re.sub(r"\bi\b", "1", t)
    return re.sub(r"\s+", " ", t).strip()


def has_word(phrase, text):
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", text) is not None


def clean_text(s):
    s = html.unescape(str(s or ""))
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def clean(v):
    if v is None or (isinstance(v, float) and v != v):
        return ""
    return str(v)


def dig(obj, path, default=""):
    for part in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(part)
        elif isinstance(obj, list) and part.isdigit() and int(part) < len(obj):
            obj = obj[int(part)]
        else:
            return default
        if obj is None:
            return default
    return obj


def mk(raw_id, title, location, url, source, description="", desc_fn=None, trusted_loc=False, company=""):
    return {"raw_id": str(raw_id), "title": clean(title).strip(), "location": clean(location).strip(),
            "url": clean(url).strip(), "source": source, "description": clean_text(description),
            "desc_fn": desc_fn, "trusted_loc": trusted_loc, "company": company, "id": ""}


def http(method, url, **kw):
    headers = {**HTTP_HEADERS, **kw.pop("headers", {})}
    last = None
    for attempt in range(3):
        try:
            r = requests.request(method, url, headers=headers, timeout=TIMEOUT, **kw)
            if r.status_code in (429, 500, 502, 503, 504):
                last = r
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            return r
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    last.raise_for_status()
    return last


# ================================================================ adapters
def a_greenhouse(slug):
    d = http("GET", f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs").json()

    def detail(jid):
        j = http("GET", f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{jid}").json()
        return {"description": clean_text(j.get("content"))}
    return [mk(j["id"], j.get("title"), (j.get("location") or {}).get("name"), j.get("absolute_url"),
               "Greenhouse", desc_fn=(lambda jid=j["id"]: detail(jid))) for j in d.get("jobs", [])]


def a_lever(slug):
    d = http("GET", f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"}).json()
    out = []
    for j in d:
        lists = " ".join(f"{x.get('text', '')} {clean_text(x.get('content'))}" for x in (j.get("lists") or []))
        desc = f"{j.get('descriptionPlain', '')} {lists} {j.get('additionalPlain', '')}"
        out.append(mk(j["id"], j.get("text"), (j.get("categories") or {}).get("location"),
                      j.get("hostedUrl"), "Lever", desc))
    return out


def a_ashby(slug):
    d = http("GET", f"https://api.ashbyhq.com/posting-api/job-board/{slug}").json()
    return [mk(j["id"], j.get("title"), j.get("location"), j.get("jobUrl") or j.get("applyUrl"), "Ashby",
               j.get("descriptionPlain") or j.get("descriptionHtml"))
            for j in d.get("jobs", []) if j.get("isListed", True)]


def a_smartrecruiters(slug):
    out, offset = [], 0
    for _ in range(5):
        d = http("GET", f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
                 params={"limit": 100, "offset": offset, "country": "in"}).json()
        if offset == 0 and not d.get("totalFound"):
            raise ValueError("no postings (wrong company id?)")
        items = d.get("content", [])
        for j in items:
            loc = j.get("location") or {}

            def detail(ref=j.get("ref")):
                a = http("GET", ref).json()
                secs = (a.get("jobAd") or {}).get("sections") or {}
                return {"description": clean_text(" ".join(str((v or {}).get("text", "")) for v in secs.values()))}
            out.append(mk(j["id"], j.get("name"), ", ".join(x for x in (loc.get("city"), loc.get("country")) if x),
                          f"https://jobs.smartrecruiters.com/{slug}/{j['id']}", "SmartRecruiters", desc_fn=detail))
        offset += 100
        if len(items) < 100:
            break
    return out


def _wd_location_facets(d):
    found = {}

    def walk(node):
        if isinstance(node, dict):
            param, vals = node.get("facetParameter"), node.get("values")
            if isinstance(vals, list):
                for v in vals:
                    if (isinstance(v, dict) and "id" in v and param
                            and re.search(r"bengaluru|bangalore", str(v.get("descriptor", "")), re.I)):
                        found.setdefault(param, []).append(v["id"])
                    walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)
    walk(d.get("facets", []))
    return found


def a_workday(tenant, wd, site):
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    first = http("POST", api, json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}).json()
    facets = _wd_location_facets(first)
    body0 = {"appliedFacets": facets, "searchText": "" if facets else "Bengaluru"}
    out, offset, total = [], 0, 0
    for _ in range(25):
        d = http("POST", api, json={**body0, "limit": 20, "offset": offset}).json()
        total = max(total, d.get("total", 0))
        posts = d.get("jobPostings", [])
        if not posts:
            break
        for p in posts:
            path = p.get("externalPath", "")

            def detail(path=path):
                x = http("GET", f"{base}/wday/cxs/{tenant}/{site}{path}").json().get("jobPostingInfo", {})
                locs = " ".join([str(x.get("location", ""))] + [str(l) for l in x.get("additionalLocations", [])])
                return {"description": clean_text(x.get("jobDescription")), "location": locs}
            out.append(mk(path, p.get("title"), p.get("locationsText"), f"{base}/{site}{path}", "Workday",
                          desc_fn=detail, trusted_loc=bool(facets)))
        offset += 20
        if offset >= total:
            break
    return out


def a_amazon():
    out = []
    for off in range(0, 300, 100):
        d = http("GET", "https://www.amazon.jobs/en/search.json", params={
            "base_query": "software development engineer", "country": "IND",
            "loc_query": "Bengaluru, Karnataka, India", "category[]": "software-development",
            "result_limit": 100, "offset": off, "sort": "recent"}).json()
        jobs = d.get("jobs", [])
        for j in jobs:
            # level lives in the qualifications text ("3+ years" ~ SDE-II), not in the title
            desc = f"{j.get('basic_qualifications', '')} {j.get('description', '')} {j.get('preferred_qualifications', '')}"
            out.append(mk(j.get("id_icims") or j.get("id"), j.get("title"),
                          j.get("normalized_location") or j.get("location"),
                          "https://www.amazon.jobs" + str(j.get("job_path", "")), "Amazon Careers", desc))
        if len(jobs) < 100:
            break
    return out


def a_atlassian():
    d = http("GET", "https://www.atlassian.com/endpoint/careers/listings").json()
    out = []
    for j in d:
        locs = j.get("locations") or j.get("location") or ""
        locs = "; ".join(locs) if isinstance(locs, list) else str(locs)
        desc = " ".join(clean_text(j.get(k)) for k in ("overview", "responsibilities", "qualifications"))
        out.append(mk(j.get("id"), j.get("title"), locs,
                      j.get("applyUrl") or f"https://www.atlassian.com/company/careers/details/{j.get('id')}",
                      "Atlassian Careers", desc))
    return out


def a_eightfold(host, domain, query="software engineer", location="Bengaluru"):
    out, errs = [], []
    # newer PCSX API first, classic v2 API second
    for kind in ("pcsx", "v2"):
        try:
            for start in range(0, 200, 25):
                if kind == "pcsx":
                    d = http("GET", f"https://{host}/api/pcsx/search", params={
                        "domain": domain, "query": query, "location": location, "start": start,
                        "sort_by": "timestamp"}).json()
                    items = dig(d, "data.positions", [])
                else:
                    d = http("GET", f"https://{host}/api/apply/v2/jobs", params={
                        "domain": domain, "query": query, "location": location, "start": start,
                        "num": 25, "sort_by": "timestamp"}).json()
                    items = d.get("positions", [])
                if not items:
                    break
                for j in items:
                    locs = j.get("locations") or j.get("location") or ""
                    locs = "; ".join(locs) if isinstance(locs, list) else str(locs)
                    pid = j.get("id")
                    url = j.get("canonicalPositionUrl") or (f"https://{host}{j['positionUrl']}" if j.get("positionUrl")
                                                              else f"https://{host}/careers/job/{pid}")

                    def detail(pid=pid):
                        x = http("GET", f"https://{host}/api/apply/v2/jobs/{pid}", params={"domain": domain}).json()
                        return {"description": clean_text(x.get("job_description"))}
                    out.append(mk(pid, j.get("name") or j.get("title"), locs, url, "Eightfold",
                                  j.get("job_description") or "", desc_fn=detail))
                if len(items) < 25:
                    break
            if out:
                return out
        except Exception as e:  # noqa: BLE001
            errs.append(f"{kind}: {e}")
    if errs:
        raise RuntimeError("; ".join(errs)[:200])
    return out


def a_microsoft():
    try:
        return a_eightfold("apply.careers.microsoft.com", "microsoft.com",
                           "software engineer", "Bengaluru, Karnataka, India")
    except Exception:  # noqa: BLE001
        out = []
        for pg in range(1, 6):
            d = http("GET", "https://gcsservices.careers.microsoft.com/search/api/v1/search", params={
                "q": "software engineer", "lc": "India", "l": "en_us", "pg": pg, "pgSz": 20,
                "o": "Recent", "flt": "true"}).json()
            jobs = d["operationResult"]["result"]["jobs"]
            if not jobs:
                break
            for j in jobs:
                locs = (j.get("properties") or {}).get("locations") or []
                out.append(mk(j["jobId"], j.get("title"), "; ".join(locs),
                              f"https://jobs.careers.microsoft.com/global/en/job/{j['jobId']}",
                              "Microsoft Careers", (j.get("properties") or {}).get("description")))
        return out


def a_oracle(host, site):
    out, base = [], f"https://{host}/hcmRestApi/resources/latest"
    for off in range(0, 200, 25):
        finder = (f"findReqs;siteNumber={site},facetsList=LOCATIONS;WORK_LOCATIONS;CATEGORIES,"
                  f"limit=25,offset={off},keyword=software engineer,sortBy=POSTING_DATES_DESC")
        d = http("GET", f"{base}/recruitingCEJobRequisitions",
                 params={"onlyData": "true", "expand": "requisitionList", "finder": finder}).json()
        reqs = (d.get("items") or [{}])[0].get("requisitionList", [])
        if not reqs:
            break
        for r in reqs:
            rid = r.get("Id")

            def detail(rid=rid):
                x = http("GET", f"{base}/recruitingCEJobRequisitionDetails",
                         params={"onlyData": "true", "finder": f'ById;Id="{rid}",siteNumber={site}'}).json()
                it = (x.get("items") or [{}])[0]
                return {"description": clean_text(f"{it.get('ExternalDescriptionStr', '')} "
                                                  f"{it.get('ExternalQualificationsStr', '')}")}
            out.append(mk(rid, r.get("Title"), r.get("PrimaryLocation"),
                          f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{rid}",
                          "Oracle HCM", desc_fn=detail))
        if len(reqs) < 25:
            break
    return out


def a_uber():
    out = []
    for page in range(5):
        d = http("POST", "https://www.uber.com/api/loadSearchJobsResults?localeCode=en",
                 json={"limit": 50, "page": page,
                       "params": {"location": [{"country": "IND", "city": "Bangalore"}], "department": ["Engineering"]}},
                 headers={"x-csrf-token": "x", "Content-Type": "application/json"}).json()
        res = dig(d, "data.results", [])
        if not res:
            break
        for j in res:
            locs = j.get("allLocations") or ([j.get("location")] if j.get("location") else [])
            loc = "; ".join(f"{x.get('city', '')}, {x.get('countryName') or x.get('country', '')}" for x in locs)
            out.append(mk(j["id"], j.get("title"), loc, f"https://www.uber.com/global/en/careers/list/{j['id']}/",
                          "Uber Careers", j.get("description")))
        if len(res) < 50:
            break
    return out


_GS_QUERY = ("query GetRoles($searchQueryInput: RoleSearchQueryInput!) { roleSearch(searchQueryInput: "
             "$searchQueryInput) { totalCount items { roleId jobTitle corporateTitle division "
             "locations { primary state country city } } } }")


def a_goldman():
    out = []
    for page in range(6):
        variables = {"searchQueryInput": {
            "page": {"pageSize": 20, "pageNumber": page},
            "sort": {"sortStrategy": "POSTED_DATE", "sortOrder": "DESC"},
            "filters": [{"filterCategoryType": "LOCATION",
                         "filters": [{"filter": "Bengaluru, Karnataka, India", "subFilters": []}]}],
            "experiences": ["PROFESSIONAL"], "searchTerm": ""}}
        d = http("POST", "https://api-higher.gs.com/gateway/api/v1/graphql",
                 json={"operationName": "GetRoles", "variables": variables, "query": _GS_QUERY},
                 headers={"Content-Type": "application/json"}).json()
        items = dig(d, "data.roleSearch.items", [])
        if not items:
            break
        for j in items:
            loc = "; ".join(f"{x.get('city', '')}, {x.get('country', '')}" for x in (j.get("locations") or []))
            out.append(mk(j["roleId"], j.get("jobTitle"), loc, f"https://higher.gs.com/roles/{j['roleId']}",
                          "Goldman Careers", f"{j.get('corporateTitle', '')} {j.get('division', '')}"))
        if len(items) < 20:
            break
    return out


def a_google():
    out, seen = [], set()
    for page in range(1, 4):
        r = http("GET", "https://www.google.com/about/careers/applications/jobs/results/",
                 params={"location": "Bengaluru, Karnataka, India", "q": "software engineer",
                         "sort_by": "date", "page": page}, headers={"Accept": "text/html"})
        found = re.findall(r"jobs/results/(\d{6,})-([a-z0-9\-]+)", r.text)
        new = [(i, s) for i, s in found if i not in seen]
        if not new:
            break
        for i, slug in new:
            seen.add(i)
            out.append(mk(i, slug.replace("-", " ").title(), "Bengaluru, India",
                          f"https://www.google.com/about/careers/applications/jobs/results/{i}-{slug}",
                          "Google Careers"))
    if not out:
        raise ValueError("no job links found in page (layout changed?)")
    return out


ADAPTERS = {"greenhouse": a_greenhouse, "lever": a_lever, "ashby": a_ashby, "smartrecruiters": a_smartrecruiters,
            "workday": a_workday, "oracle": a_oracle, "eightfold": a_eightfold, "amazon": a_amazon,
            "atlassian": a_atlassian, "microsoft": a_microsoft, "uber": a_uber, "goldman": a_goldman,
            "google": a_google}
VERIFY_INDIA = {"greenhouse", "lever", "ashby", "smartrecruiters"}   # generic ATS slugs need a sanity check
INDIA_HINT = re.compile(r"india|bengaluru|bangalore|mumbai|delhi|gurgaon|gurugram|hyderabad|pune|chennai|noida", re.I)


def validate(company, spec, jobs):
    """Guard against slug collisions (e.g. an unrelated company called 'zeta')."""
    if spec[0] not in VERIFY_INDIA:
        return True
    if any(INDIA_HINT.search(j["location"]) for j in jobs):
        return True
    if spec[0] == "greenhouse":
        try:
            name = http("GET", f"https://boards-api.greenhouse.io/v1/boards/{spec[1]}").json().get("name", "")
            return slugify(company["alias"]) in slugify(name) or slugify(company["name"]) in slugify(name)
        except Exception:  # noqa: BLE001
            return False
    return False


def fetch_company(c, cached, fails):
    """-> (jobs|None, spec|None, last_error, was_cached)."""
    use_cached = bool(cached) and fails < 3
    specs = [tuple(cached)] if use_cached else list(c["src"])
    last = None
    for spec in specs:
        try:
            jobs = ADAPTERS[spec[0]](*spec[1:])
            if use_cached or validate(c, spec, jobs):
                return jobs, spec, None, use_cached
            last = f"{spec[0]}:{spec[1] if len(spec) > 1 else ''} rejected (does not look like {c['name']})"
        except Exception as e:  # noqa: BLE001
            last = f"{spec[0]}: {type(e).__name__}: {str(e)[:110]}"
    return None, None, last, use_cached


# ================================================================ filtering
def is_valid_location(job):
    if job.get("trusted_loc"):
        return True
    loc = job["location"].lower()
    if re.search(r"\b\d+\s+locations?\b", loc):      # Workday "2 Locations": resolved after detail fetch
        return True
    return not loc or any(k in loc for k in LOCATION_KEYWORDS)


def classify_title(norm):
    if any(has_word(x, norm) for x in EXCLUDE):
        return "no"
    if any(has_word(x, norm) for x in INCLUDE):
        return "yes"
    if GENERIC_TITLE.search(norm):
        return "maybe"
    return "no"


def phase1(job):
    return is_valid_location(job) and classify_title(normalize_title(job["title"])) != "no"


_RANGE = re.compile(r"(\d{1,2})\s*(?:years?|yrs?|yoe)?\s*(?:-|–|—|to)\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?|yoe)\b", re.I)
_PLUS = re.compile(r"(\d{1,2})\s*\+\s*(?:years?|yrs?|yoe)\b", re.I)
_PLAIN = re.compile(r"(\d{1,2})\s*(?:years?|yrs?)\b", re.I)
_EXP = re.compile(r"experience|\bexp\b|yoe", re.I)


def yoe_from(text, need_exp_word):
    """-> list of (lo, hi|None) experience requirements found in text."""
    out, taken = [], []
    for pat, kind in ((_RANGE, "r"), (_PLUS, "p"), (_PLAIN, "n")):
        for m in pat.finditer(text or ""):
            if any(a <= m.start() < b for a, b in taken):
                continue
            if need_exp_word and not _EXP.search(text[max(0, m.start() - 80): m.end() + 80]):
                continue
            taken.append((m.start(), m.end()))
            lo = int(m.group(1))
            hi = int(m.group(2)) if kind == "r" else None
            if lo > 20 or (hi is not None and hi < lo):
                continue
            out.append((lo, hi))
    return out


def evaluate(job):
    """-> (ok, info dict). Uses whatever data the job has (title, description)."""
    norm = normalize_title(job["title"])
    kind = classify_title(norm)
    if kind == "no" or not is_valid_location(job):
        return False, {}
    soft = any(has_word(w, norm) for w in SOFT_SENIOR)
    lo = hi = None
    tr = yoe_from(job["title"], False)
    if tr:
        lo, hi = tr[0]
    else:
        dr = yoe_from(job["description"], True)
        if dr:
            lo = max(l for l, _ in dr)
    if lo is not None and lo > (MAX_MIN_YOE_SENIOR if soft else MAX_MIN_YOE):
        return False, {}
    if hi is not None and hi < MIN_MAX_YOE:
        return False, {}
    if soft and lo is None:
        return False, {}
    d = job["description"].lower()
    tags = []
    if re.search(r"\bjava\b", d):
        tags.append("Java")
    if re.search(r"\bspring\b", d):
        tags.append("Spring")
    if re.search(r"\b(backend|back end|microservices)\b", d):
        tags.append("backend")
    if kind == "maybe" and not tags:
        return False, {}
    exp = None if lo is None else (f"{lo}-{hi} yrs" if hi else f"{lo}+ yrs")
    return True, {"exp": exp, "tags": tags}


def filter_cfg_hash():
    blob = json.dumps([INCLUDE, EXCLUDE, SOFT_SENIOR, LOCATION_KEYWORDS, MAX_MIN_YOE, MAX_MIN_YOE_SENIOR,
                       MIN_MAX_YOE, GENERIC_TITLE.pattern, 4])
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


# ================================================================== state
def new_state():
    return {"v": 4, "seen": set(), "sources": set(), "resolved": {}, "fails": {}, "fps": set(),
            "cfg": "", "agg_cursor": 0, "last_hb": 0, "alerts": []}


def load_state():
    st = new_state()
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return st
    if isinstance(data, dict) and data.get("v") == 4:   # older formats are simply re-seeded silently
        for k in ("seen", "sources", "fps"):
            st[k] = set(data.get(k, []))
        for k in ("resolved", "fails", "cfg", "agg_cursor", "last_hb", "alerts"):
            st[k] = data.get(k, st[k])
    return st


def save_state(st):
    out = {**st, "seen": sorted(st["seen"]), "sources": sorted(st["sources"]), "fps": sorted(st["fps"])}
    with open(STATE_FILE, "w") as f:
        json.dump(out, f, indent=1)


# ============================================================ aggregator
def agg_search(term, company=None):
    from jobspy import scrape_jobs  # lazy: direct sources work without it
    df = scrape_jobs(site_name=AGG_SITES, search_term=term,
                     google_search_term=f"{term} jobs near Bengaluru, Karnataka, India since last 3 days",
                     location="Bengaluru, Karnataka, India", results_wanted=15, hours_old=48,
                     country_indeed="India", description_format="markdown")
    out = []
    if df is None or df.empty:
        return out
    key = slugify(company["alias"]) if company else ""
    for _, row in df.iterrows():
        url = clean(row.get("job_url"))
        comp = clean(row.get("company")) or "Unknown"
        if not url or (key and key not in slugify(comp) and slugify(company["name"]) not in slugify(comp)):
            continue
        j = mk("agg_" + hashlib.sha1(url.encode()).hexdigest()[:16], row.get("title"), row.get("location"), url,
               clean(row.get("site")).title() or "JobSpy", clean(row.get("description")),
               company=company["name"] if company else canon(comp))
        out.append(j)
    return out


def canon(raw):
    """Map a free-text company name from a job board onto the registry name."""
    r = slugify(raw)
    for c in COMPANIES:
        for k in {slugify(c["alias"]), slugify(c["name"])}:
            if k and len(k) >= 4 and (k in r or r == k):
                return c["name"]
    return raw


def fp_of(job):
    return "|".join([slugify(job["company"]), slugify(normalize_title(job["title"])), "blr"])


# ================================================================ telegram
def tg_post(text, preview=False):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": not preview}
    for _ in range(3):
        try:
            res = requests.post(url, json=payload, timeout=15)
        except requests.RequestException as e:
            print(f"Telegram network error: {e}")
            time.sleep(2)
            continue
        if res.status_code == 200:
            time.sleep(1)
            return True
        if res.status_code == 429:
            try:
                wait = res.json().get("parameters", {}).get("retry_after", 5)
            except ValueError:
                wait = 5
            time.sleep(wait + 1)
            continue
        print(f"Telegram API error ({res.status_code}): {res.text}")
        return False
    return False


E = lambda s: html.escape(str(s), quote=True)  # noqa: E731
FIT_ICON = {"High": "☕ High Java/Spring fit", "Medium": "◐ Medium Java fit", "Low": "◔ Low Java fit"}


def fmt_alert(a):
    j, info, meta = a["job"], a["info"], META.get(a["job"]["company"], {})
    lines = ["🚨 <b>NEW OPENING</b>", "",
             f"🏢 <b>{E(j['company'])}</b>" + (f"  ·  {FIT_ICON[meta['fit']]}" if meta.get("fit") in FIT_ICON else ""),
             f"💼 {E(j['title'])}", f"📍 {E(j['location'] or 'Not specified')}"]
    bits = ([f"Exp {info['exp']}"] if info.get("exp") else []) + ([", ".join(info["tags"]) + " in JD"] if info.get("tags") else [])
    if bits:
        lines.append("🧾 " + " · ".join(E(b) for b in bits))
    if meta.get("comp") and meta["comp"] != "?":
        lines.append(f"💰 ~{E(meta['comp'])} (est.)" + (f"  ·  🎯 {meta['diff']}/10" if meta.get("diff") else ""))
    lines += [f"🌐 {E(j['source'])}", "", f'🔗 <a href="{E(j["url"])}">Apply</a>']
    return "\n".join(lines)


def send_digest(alerts):
    by = {}
    for a in alerts:
        by.setdefault(a["job"]["company"], []).append(a)
    chunks, cur = [], "🚨 <b>NEW OPENINGS</b>\n\n"
    for comp, items in by.items():
        block = f"🏢 <b>{E(comp)}</b> ({len(items)})\n" + "".join(
            f'• <a href="{E(a["job"]["url"])}">{E(a["job"]["title"])}</a>'
            + (f" — {E(a['info']['exp'])}" if a["info"].get("exp") else "") + "\n" for a in items[:12]) + "\n"
        if len(cur) + len(block) > 3500:
            chunks.append(cur)
            cur = ""
        cur += block
    chunks.append(cur)
    results = [tg_post(c) for c in chunks]
    return all(results)


# ===================================================================== main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--check-agg", action="store_true")
    args = ap.parse_args()
    check = args.check or args.check_agg
    if not check and not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        sys.exit("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set - refusing to run "
                 "(jobs would be marked as seen without alerting).")

    st = load_state()
    h = filter_cfg_hash()
    if st["cfg"] != h:   # filters changed -> re-index silently instead of flooding you
        st["sources"], st["cfg"] = set(), h

    # ---- A. fetch every company in parallel
    with ThreadPoolExecutor(WORKERS) as pool:
        results = list(pool.map(
            lambda c: (c,) + fetch_company(c, st["resolved"].get(c["name"]), st["fails"].get(c["name"], 0)),
            COMPANIES))

    seeded, warnings, unresolved, report, pending = [], [], [], [], []
    for c, jobs, spec, err, was_cached in results:
        name, slug = c["name"], slugify(c["name"])
        if jobs is None:
            if was_cached:
                st["fails"][name] = st["fails"].get(name, 0) + 1
                if st["fails"][name] == FAIL_ALERT_AFTER:
                    warnings.append(f"{name}: direct source failing ({err}); falling back to search")
            unresolved.append(c)
            report.append((name, "FALLBACK", err or "no direct source configured", 0, 0, []))
            continue
        st["fails"][name] = 0
        st["resolved"][name] = list(spec)
        for j in jobs:
            j["company"], j["id"] = name, f"{slug}|{j['raw_id']}"
        cand = [j for j in jobs if phase1(j)]
        report.append((name, f"{spec[0]}:{spec[1] if len(spec) > 1 else ''}", "", len(jobs), len(cand), cand))
        if check:
            pending += [(c, j) for j in cand[:10]]
            continue
        if name not in st["sources"]:        # first sight: index silently, never flood
            st["seen"].update(j["id"] for j in cand)
            st["sources"].add(name)
            seeded.append(name)
            continue
        pending += [(c, j) for j in cand if j["id"] not in st["seen"]]

    # ---- B. fetch descriptions only for new candidates
    need = [j for _, j in pending if j["desc_fn"] and not j["description"]]
    deferred = set(id(j) for j in need[DETAIL_CAP:])

    def get_detail(j):
        try:
            return j["desc_fn"]() or {}
        except Exception as e:  # noqa: BLE001
            print(f"  detail failed for {j['company']} / {j['title']}: {str(e)[:80]}")
            return {}
    with ThreadPoolExecutor(WORKERS) as pool:
        for j, res in zip(need[:DETAIL_CAP], pool.map(get_detail, need[:DETAIL_CAP])):
            j["description"] = res.get("description", "") or j["description"]
            if res.get("location") is not None and not j["trusted_loc"]:
                j["location"] = f"{j['location']} {res['location']}".strip()

    alerts, evaluated_ids = [], []
    for c, j in pending:
        if id(j) in deferred:
            continue
        ok, info = evaluate(j)
        evaluated_ids.append(j["id"])
        if ok:
            alerts.append({"job": j, "info": info, "origin": "direct"})

    # ---- C. fallback search for companies with no working direct source (+ generic queries)
    agg_report = []
    if (not DISABLE_AGG) and (not check or args.check_agg):
        queue = [("generic", q, None) for q in AGG_GENERIC] + [("company", f"{c['alias']} software engineer java backend", c)
                                                                for c in unresolved]
        n = len(queue)
        take = [queue[(st["agg_cursor"] + i) % n] for i in range(min(AGG_BUDGET if not check else 2, n))] if n else []
        if not check:
            st["agg_cursor"] = (st["agg_cursor"] + len(take)) % n if n else 0
        for kind, term, comp in take:
            key = f"agg:{term}"
            try:
                jobs = agg_search(term, comp)
            except Exception as e:  # noqa: BLE001
                agg_report.append((term, f"FAIL {type(e).__name__}: {str(e)[:80]}"))
                continue
            cand = [j for j in jobs if phase1(j)]
            for j in cand:
                j["id"] = j["raw_id"]
            agg_report.append((term, f"{len(jobs)} fetched, {len(cand)} candidates"))
            if check:
                alerts += [{"job": j, "info": i, "origin": "agg"} for j in cand for ok, i in [evaluate(j)] if ok]
                continue
            if key not in st["sources"]:
                st["seen"].update(j["id"] for j in cand)
                st["sources"].add(key)
                seeded.append(f"search: {term}")
                continue
            for j in cand:
                if j["id"] in st["seen"]:
                    continue
                ok, info = evaluate(j)
                evaluated_ids.append(j["id"])
                if ok and fp_of(j) not in st["fps"]:
                    alerts.append({"job": j, "info": info, "origin": "agg"})

    # ---- de-duplicate within this run (same job returned by several searches / by direct + search)
    uniq, ids, fps = [], set(), set()
    for a in alerts:
        j = a["job"]
        if j["id"] in ids or (a["origin"] == "agg" and fp_of(j) in fps):
            continue
        ids.add(j["id"])
        fps.add(fp_of(j))
        uniq.append(a)
    alerts = uniq

    # ---- check mode: report and exit
    if check:
        print(f"\n{'COMPANY':24} {'SOURCE':34} {'JOBS':>5} {'BLR+ROLE':>9}  SAMPLE ACCEPTED")
        acc_by = {}
        for a in alerts:
            acc_by.setdefault(a["job"]["company"], []).append(a["job"]["title"])
        for name, src, err, n, p, _ in report:
            sample = " | ".join(acc_by.get(name, [])[:2])
            print(f"{name[:24]:24} {src[:34]:34} {n:>5} {p:>9}  {sample[:70]}" + (f"   [{err[:70]}]" if err else ""))
        direct = sum(1 for r in report if r[1] != "FALLBACK")
        print(f"\nDirect: {direct}/{len(report)} companies | fallback-only: {len(report) - direct}")
        for t, r in agg_report:
            print(f"  search '{t}': {r}")
        return

    # ---- D. send, then persist only what was really delivered
    order = {"High": 0, "Medium": 1, "Low": 2, "?": 3}
    alerts.sort(key=lambda a: (order.get(META.get(a["job"]["company"], {}).get("fit", "?"), 3),
                               META.get(a["job"]["company"], {}).get("diff") or 99))
    delivered = set()
    if len(alerts) > DIGEST_THRESHOLD:
        if send_digest(alerts):
            delivered = {a["job"]["id"] for a in alerts}
    else:
        for a in alerts:
            if tg_post(fmt_alert(a), preview=True):
                delivered.add(a["job"]["id"])
    sent = len(delivered)
    for a in alerts:
        if a["job"]["id"] in delivered:
            st["fps"].add(fp_of(a["job"]))
    st["seen"].update(evaluated_ids)                       # evaluated (accepted or rejected): don't redo
    st["seen"].difference_update({a["job"]["id"] for a in alerts} - delivered)   # undelivered alerts retry
    now = time.time()
    st["alerts"] = [t for t in st["alerts"] if now - t < 86400] + [now] * sent

    # ---- E. status message: first run / daily heartbeat / warnings
    direct = sum(1 for r in report if r[1] != "FALLBACK")
    if seeded or warnings or now - st["last_hb"] > HEARTBEAT_HOURS * 3600 - 600:
        msg = [f"✅ <b>Job tracker status</b>",
               f"Direct career-API coverage: {direct}/{len(report)} companies",
               f"Fallback (search) only: {len(report) - direct}",
               f"Alerts in last 24h: {len(st['alerts'])}"]
        if seeded:
            msg.append(f"Newly indexed (silent): {E(', '.join(seeded)[:600])}")
        msg += [f"⚠️ {E(w)}" for w in warnings]
        if tg_post("\n".join(msg)):
            st["last_hb"] = now
    save_state(st)
    print(f"Done. {len(alerts)} alert(s), {sent} sent, direct {direct}/{len(report)}, seeded {len(seeded)}.")


if __name__ == "__main__":
    main()

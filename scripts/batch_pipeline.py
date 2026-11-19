#!/usr/bin/env python3
"""Reusable batch pipeline for LinkedIn prospect sequences (see prospect-to-sequence skill).

Usage:
  python3 batch_pipeline.py <batch_dir> <command> [options]

Commands:
  enrich    prospects_raw.json -> profiles_raw.json + profiles.json (current role, tenure, work history) via Apify.
            Prints the cost estimate and refuses to spend until you pass --yes. Key comes from $APIFY_KEY or --env.
  qualify   prospects_raw.json + {prefix}qual_out_*.json -> prospects.json (FIT, plus MAYBE with --include-maybe),
            excluded.json (with reasons), qualification-report.csv
  split     prospects.json (+ posts.json) -> {prefix}batch_{i}.json, N prospects per writer agent
  build_fc  {prefix}out_{i}.json + {prefix}batch_{i}.json -> {prefix}fc_batch_{i}.json for the fact-check agents
  apply_fc  {prefix}fc_out_*.json -> corrections written into {prefix}out_{i}.json, fact-check-report.csv
  merge     -> <name>-sequences.csv, <name>-send-ready.csv, heyreach_leads_payload.json, payload_chunk_{k}.json
  check     lint the merged output: "!", emoji, empty fields, apostrophes in name fields, near-duplicate
            wording between people at the same company, over-long messages
  diff_list <dump1> [dump2 ...]  compare the payload with a get_leads_from_list dump, print missing leads

Options:
  --prefix P     file prefix inside <batch_dir> (batch 3 used "li3_"; default "")
  --name NAME    output file stem (default: the batch folder name)
  --writers N    number of writer outputs to read (default 10)
  --per N        prospects per writer batch for `split` (default 15)
  --chunk N      leads per upload chunk (default 25)
  --actor A      enrich: Apify actor (default harvestapi~linkedin-profile-scraper, about $0.004 per profile)
  --max-usd X    enrich: hard spend cap passed to Apify (default 2.0)
  --env FILE     enrich: .env file holding APIFY_KEY (default: <batch_dir>/../.env)
  --yes          enrich: confirm the spend
  --include-maybe  qualify: keep MAYBE prospects too (default: only FIT; MAYBE rows go to maybe.json for review)

Expected qualification output (one JSON list per qual_out file): profile_url, verdict (FIT|MAYBE|NO), reason,
evidence (list of short quotes or URLs), current_role_confirmed, company_type, tenure_months.
Expected writer output (one JSON list per file): profile_url, full_name, first_name, last_name, job_title,
company, location, message_1, message_2, message_3, the_ask, facts_used, post_used, confidence.
Expected fact-check output: profile_url, full_name, verdict (CLEAN|FIX|HOLD), hold_reason,
corrected_message_2, corrected_message_3, claims[{claim, where, status, evidence}].
"""
import argparse, csv, difflib, glob, json, os, re, sys, urllib.request, urllib.error
from collections import Counter, defaultdict

COLS = ["Profile URL", "First Name", "Last Name", "Job Title", "Company", "Location", "Status", "Connection Note",
        "Message 1", "Delay 1 (days)", "Message 2", "Delay 2 (days)", "Message 3", "Delay 3 (days)",
        "The Ask (send manually after reply)", "Facts Used", "Confidence", "Post Used", "Compliment Source",
        "Fact Check", "Hold Reason"]
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
MAX_MSG = 1500  # LinkedIn message limit is higher, this keeps copy readable


class Ctx:
    def __init__(self, a):
        self.D = os.path.abspath(a.batch_dir)
        self.p = a.prefix
        self.name = a.name or os.path.basename(self.D)
        self.n = a.writers
        self.per = a.per
        self.chunk = a.chunk

    def path(self, stem, i=None):
        return os.path.join(self.D, f"{self.p}{stem}" + (f"_{i}" if i is not None else "") + ".json")

    def outs(self):
        return [(i, json.load(open(self.path("out", i)))) for i in range(1, self.n + 1) if os.path.exists(self.path("out", i))]


ACTOR_MODE = "Profile details no email ($4 per 1k)"
PRICE_PER_PROFILE = 0.004


def months_from(duration):
    """'1 yr 10 mos' -> 22"""
    if not duration:
        return None
    y = re.search(r"(\d+)\s*yr", duration)
    m = re.search(r"(\d+)\s*mo", duration)
    if not (y or m):
        return None
    return (int(y.group(1)) * 12 if y else 0) + (int(m.group(1)) if m else 0)


def slim_profile(x):
    exp = x.get("experience") or []
    cur = (x.get("currentPosition") or [{}])[0]
    cur_co = (cur.get("companyName") or "").strip()
    tenure = None
    for e in exp:
        if (e.get("companyName") or "").strip() == cur_co and not _ended(e):
            tenure = months_from(e.get("duration"))
            break
    hist = []
    for e in exp[:5]:
        sd, ed = e.get("startDate"), e.get("endDate")
        hist.append({"title": e.get("position"), "company": e.get("companyName"), "duration": e.get("duration"),
                     "start": sd.get("text") if isinstance(sd, dict) else sd, "end": ed.get("text") if isinstance(ed, dict) else ed})
    loc = x.get("location")
    return {"public_identifier": x.get("publicIdentifier"), "linkedin_url": x.get("linkedinUrl"), "headline": x.get("headline"),
            "current_title": cur.get("position") or cur.get("title"), "current_company": cur_co, "tenure_months": tenure,
            "location": loc.get("linkedinText") if isinstance(loc, dict) else loc, "history": hist}


def _ended(e):
    ed = e.get("endDate")
    t = ed.get("text") if isinstance(ed, dict) else ed
    return bool(t) and str(t).lower() != "present"


def read_env_key(path, name="APIFY_KEY"):
    if os.environ.get(name):
        return os.environ[name]
    if path and os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip("\"'")
    return ""


def ident(url):
    return url.split("/in/")[-1].split("?")[0].strip("/").lower()


def enrich(c, actor, max_usd, env_path, yes):
    raw_path = os.path.join(c.D, "prospects_raw.json")
    if not os.path.exists(raw_path):
        sys.exit("prospects_raw.json not found: save the analyzed list there first")
    raw = json.load(open(raw_path))
    urls = [p["profile_url"].replace("http://", "https://") for p in raw if p.get("profile_url")]
    est = len(urls) * PRICE_PER_PROFILE
    print(f"{len(urls)} profiles -> estimated cost ${est:.2f} (actor {actor}, cap ${max_usd:.2f})")
    if not yes:
        sys.exit("not spending anything: re-run with --yes to confirm")
    key = read_env_key(env_path or os.path.join(c.D, "..", ".env"))
    if not key:
        sys.exit("no APIFY_KEY found: add it to the client .env yourself (do not paste it into chat) or export APIFY_KEY")
    out = []
    for i in range(0, len(urls), 100):
        body = json.dumps({"urls": urls[i:i + 100], "profileScraperMode": ACTOR_MODE}).encode()
        req = urllib.request.Request(
            f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items?maxTotalChargeUsd={max_usd}&timeout=280",
            data=body, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            out += json.load(urllib.request.urlopen(req, timeout=300))
        except urllib.error.HTTPError as e:
            sys.exit(f"Apify returned HTTP {e.code}" + (" (key rejected: update APIFY_KEY in .env)" if e.code == 401 else ""))
    json.dump(out, open(os.path.join(c.D, "profiles_raw.json"), "w"))
    byid = {}
    for x in out:
        for k in (x.get("publicIdentifier"), (x.get("linkedinUrl") or "").split("/in/")[-1]):
            if k:
                byid[k.strip("/").lower()] = x
    slim, missing = {}, []
    for p in raw:
        x = byid.get(ident(p["profile_url"]))
        if x:
            slim[p["profile_url"]] = slim_profile(x)
        else:
            missing.append(p.get("full_name") or p["profile_url"])
    json.dump(slim, open(os.path.join(c.D, "profiles.json"), "w"), indent=1)
    print(f"profiles returned: {len(out)} | matched to prospects: {len(slim)} | unmatched: {len(missing)}")
    for m in missing:
        print("  UNMATCHED", m)


CODES = ("wrong_industry", "wrong_business_model", "too_small", "wrong_role", "changed_jobs", "no_evidence")


def reason_code(reason):
    r = (reason or "").strip().lower()
    for code in CODES:
        if r.startswith(code):
            return code
    return "other"


def same_company(a, b):
    tok = lambda t: {w for w in re.sub(r"[^a-z0-9 ]", " ", (t or "").lower()).split() if w not in ("inc", "llc", "the", "of", "and", "group", "health", "healthcare", "co", "corp")}
    x, y = tok(a), tok(b)
    return bool(x and y and (x & y))


def qualify(c, include_maybe=False):
    raw_path = os.path.join(c.D, "prospects_raw.json")
    if not os.path.exists(raw_path):
        sys.exit("prospects_raw.json not found: the analyzed list must be saved there before qualification")
    raw = json.load(open(raw_path))
    prof_path = os.path.join(c.D, "profiles.json")
    profiles = json.load(open(prof_path)) if os.path.exists(prof_path) else {}
    files = sorted(glob.glob(os.path.join(c.D, f"{c.p}qual_out_*.json")))
    qual = {}
    for p in files:
        for r in json.load(open(p)):
            qual[r["profile_url"]] = r
    print(f"qualification results: {len(qual)} prospects from {len(files)} files; {len(raw)} in raw list")
    keep, maybe, excluded, report = [], [], [], [["Prospect", "Company", "Verdict", "Reason", "Role confirmed", "Company type", "Tenure (months)", "Evidence", "Profile company", "Profile title", "Role check"]]
    for pr in raw:
        q = qual.get(pr["profile_url"])
        name = pr.get("full_name") or f"{pr.get('first_name', '')} {pr.get('last_name', '')}".strip()
        if not q:
            excluded.append({**pr, "exclusion_code": "not_qualified_yet", "exclusion_reason": "not_qualified_yet"})
            report.append([name, pr.get("company", ""), "MISSING", "no qualification result", "", "", "", "", "", "", ""])
            continue
        v = q.get("verdict", "MAYBE").upper()
        ev = " | ".join(map(str, q.get("evidence", []))) if isinstance(q.get("evidence"), list) else q.get("evidence", "")
        pf = profiles.get(pr["profile_url"])
        role_check = "no profile" if not pf else ("match" if same_company(pr.get("company", ""), pf.get("current_company", "")) else "MISMATCH")
        if role_check == "MISMATCH" and v == "FIT":
            v = "MAYBE"
            q = {**q, "reason": f"role_check_mismatch: list says {pr.get('company', '')}, profile says {pf.get('current_company', '')}. " + q.get("reason", "")}
        report.append([name, pr.get("company", ""), v, q.get("reason", ""), q.get("current_role_confirmed", ""), q.get("company_type", ""),
                       q.get("tenure_months", pf.get("tenure_months", "") if pf else ""), ev, pf.get("current_company", "") if pf else "", pf.get("current_title", "") if pf else "", role_check])
        row = {**pr, "qualification": v, "qualification_reason": q.get("reason", ""), "company_type": q.get("company_type", "")}
        if v == "FIT":
            keep.append(row)
        elif v == "MAYBE":
            (keep if include_maybe else maybe).append(row)
        else:
            excluded.append({**row, "exclusion_code": reason_code(q.get("reason")), "exclusion_reason": q.get("reason", "no_fit")})
    json.dump(keep, open(os.path.join(c.D, "prospects.json"), "w"), indent=1)
    json.dump(excluded, open(os.path.join(c.D, "excluded.json"), "w"), indent=1)
    if maybe:
        json.dump(maybe, open(os.path.join(c.D, "maybe.json"), "w"), indent=1)
    with open(os.path.join(c.D, "qualification-report.csv"), "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(report)
    vd = Counter(r[2] for r in report[1:])
    print(f"verdicts: {dict(vd)} | kept: {len(keep)} | maybe held for review: {len(maybe)} | excluded: {len(excluded)}")
    if profiles:
        print("role check vs profile:", dict(Counter(r[10] for r in report[1:])))
    print("top exclusion reasons:", dict(Counter(e.get("exclusion_code") or e["exclusion_reason"] for e in excluded).most_common(6)))
    for m in maybe[:10]:
        print(f"  MAYBE {m.get('first_name', '')} {m.get('last_name', '')} ({m.get('company', '')}): {m['qualification_reason'][:90]}")


def split(c):
    prospects = json.load(open(os.path.join(c.D, "prospects.json")))
    posts_path = os.path.join(c.D, "posts.json")
    posts = {}
    if os.path.exists(posts_path):
        for p in json.load(open(posts_path)):
            posts[p.get("profile_url") or p.get("profileUrl")] = p.get("recent_posts") or p.get("posts") or []
    for p in prospects:
        p["recent_posts"] = posts.get(p["profile_url"], [])
    k = 0
    for i in range(0, len(prospects), c.per):
        k += 1
        json.dump(prospects[i:i + c.per], open(c.path("batch", k), "w"), indent=1)
    have = sum(1 for p in prospects if p["recent_posts"])
    print(f"{len(prospects)} prospects -> {k} writer batches of up to {c.per}; {have} have posts")


def build_fc(c):
    keys = ("profile_url", "full_name", "first_name", "job_title", "company", "location", "message_2", "message_3",
            "the_ask", "facts_used", "post_used", "confidence")
    for i, d in c.outs():
        posts = {p["profile_url"]: p.get("recent_posts", []) for p in json.load(open(c.path("batch", i)))}
        fc = [{k: s.get(k) for k in keys} | {"raw_recent_posts": posts.get(s["profile_url"], [])} for s in d]
        json.dump(fc, open(c.path("fc_batch", i), "w"), indent=1)
        print(f"fc batch {i}: {len(fc)}")


def apply_fc(c):
    files = sorted(glob.glob(os.path.join(c.D, f"{c.p}fc_out_*.json")))
    fc = {}
    for p in files:
        for r in json.load(open(p)):
            fc[r["profile_url"]] = r
    print(f"fact-check results: {len(fc)} prospects from {len(files)} files")
    applied = 0
    for i, d in c.outs():
        for s in d:
            r = fc.get(s["profile_url"])
            if not r:
                continue
            s["fact_check"] = r["verdict"]
            s["hold_reason"] = r.get("hold_reason") or ""
            if r["verdict"] in ("FIX", "HOLD"):
                if r.get("corrected_message_2"):
                    s["message_2"] = r["corrected_message_2"]; applied += 1
                if r.get("corrected_message_3"):
                    s["message_3"] = r["corrected_message_3"]; applied += 1
        json.dump(d, open(c.path("out", i), "w"), indent=1)
    rep = [["Prospect", "Verdict", "Claim", "Where", "Status", "Evidence"]]
    st, vd = Counter(), Counter()
    for r in fc.values():
        vd[r["verdict"]] += 1
        for cl in r.get("claims", []):
            st[cl["status"]] += 1
            rep.append([r["full_name"], r["verdict"], cl["claim"], cl["where"], cl["status"], cl["evidence"]])
    with open(os.path.join(c.D, "fact-check-report.csv"), "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rep)
    print("corrections applied:", applied, "| verdicts:", dict(vd), "| claims:", dict(st))
    for r in fc.values():
        if r["verdict"] == "HOLD":
            print(f"  HOLD {r['full_name']:28s} {(r.get('hold_reason') or '')[:90]}")
    missing = sum(1 for _, d in c.outs() for s in d if s["profile_url"] not in fc)
    if missing:
        print(f"WARNING: {missing} prospects have no fact-check result yet; they will not be send-ready")


def payload_name(first, last):
    strip = lambda t: re.sub(r"['’‘`\"]", "", t or "")
    last = strip(re.sub(r",.*$", "", last or "")).strip()
    return strip(first), last or "-"  # HeyReach silently drops leads with an empty last name


def merge(c):
    rows = []
    for _, d in c.outs():
        for s in d:
            facts = s.get("facts_used", [])
            facts = " | ".join(map(str, facts)) if isinstance(facts, list) else facts
            used_post = (s.get("post_used") or "none").lower() != "none"
            rows.append({"Profile URL": s["profile_url"], "First Name": s["first_name"], "Last Name": s.get("last_name", ""),
                         "Job Title": s["job_title"], "Company": s["company"], "Location": s.get("location", ""),
                         "Status": "qualified", "Connection Note": "", "Message 1": s["message_1"], "Delay 1 (days)": 0,
                         "Message 2": s["message_2"], "Delay 2 (days)": 1, "Message 3": s["message_3"], "Delay 3 (days)": 2,
                         "The Ask (send manually after reply)": s["the_ask"], "Facts Used": facts,
                         "Confidence": s.get("confidence", ""), "Post Used": s.get("post_used", ""),
                         "Compliment Source": "recent linkedin post" if used_post else "profile / website research",
                         "Fact Check": s.get("fact_check", ""), "Hold Reason": s.get("hold_reason", "")})
    ready = [r for r in rows if r["Fact Check"] in ("CLEAN", "FIX")]
    for fn, data in ((f"{c.name}-sequences.csv", rows), (f"{c.name}-send-ready.csv", ready)):
        with open(os.path.join(c.D, fn), "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLS); w.writeheader(); w.writerows(data)
    payload = []
    for r in ready:
        fn_, ln_ = payload_name(r["First Name"], r["Last Name"])
        payload.append({"profileUrl": r["Profile URL"].replace("http://", "https://"), "firstName": fn_, "lastName": ln_,
                        "companyName": re.sub(r"['’‘`\"]", "", r["Company"] or ""), "position": r["Job Title"],
                        "location": r["Location"] or None,
                        "customUserFields": [{"name": "MSG2", "value": r["Message 2"]}, {"name": "MSG3", "value": r["Message 3"]}]})
    json.dump(payload, open(os.path.join(c.D, "heyreach_leads_payload.json"), "w"))
    for old in glob.glob(os.path.join(c.D, "payload_chunk_*.json")):
        os.remove(old)
    for k in range(0, len(payload), c.chunk):
        json.dump(payload[k:k + c.chunk], open(os.path.join(c.D, f"payload_chunk_{k // c.chunk + 1}.json"), "w"), separators=(",", ":"))
    print(f"rows: {len(rows)} | send-ready: {len(ready)} | chunks: {-(-len(payload) // c.chunk)} | "
          f"confidence: {dict(Counter(r['Confidence'] for r in rows))} | fact check: {dict(Counter(r['Fact Check'] for r in rows))}")
    check(c, rows)


def check(c, rows=None):
    if rows is None:
        path = os.path.join(c.D, f"{c.name}-sequences.csv")
        rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    issues = []
    by_co = defaultdict(list)
    for r in rows:
        who = f"{r['First Name']} {r['Last Name']} ({r['Company']})"
        for k in ("Message 1", "Message 2", "Message 3"):
            t = r.get(k) or ""
            if not t.strip():
                issues.append(f"EMPTY      {who}: {k}")
            if "!" in t:
                issues.append(f"EXCLAIM    {who}: {k}")
            if EMOJI.search(t):
                issues.append(f"EMOJI      {who}: {k}")
            if len(t) > MAX_MSG:
                issues.append(f"TOO LONG   {who}: {k} ({len(t)} chars)")
        if not r.get("Profile URL"):
            issues.append(f"NO URL     {who}")
        if re.search(r"['’\"`]", (r.get("First Name") or "") + (r.get("Company") or "")):
            issues.append(f"QUOTE IN NAME FIELD (stripped in payload) {who}")
        if r["Fact Check"] == "HOLD":
            issues.append(f"HOLD       {who}: {r['Hold Reason'][:80]}")
        by_co[(r["Company"] or "").lower()].append(r)
    for co, rs in by_co.items():
        if not co or len(rs) < 2:
            continue
        for a in range(len(rs)):
            for b in range(a + 1, len(rs)):
                for k in ("Message 2", "Message 3"):
                    s = difflib.SequenceMatcher(None, rs[a][k], rs[b][k]).ratio()
                    if s > 0.8:
                        issues.append(f"NEAR-DUPE  {rs[a]['First Name']} / {rs[b]['First Name']} at {co}: {k} {s:.0%} similar")
    print(f"check: {len(rows)} rows, {len(issues)} flags")
    for i in issues:
        print("  " + i)


def diff_list(c, dumps):
    txt = "".join(open(d, encoding="utf-8").read() for d in dumps)
    have = {u.lower().rstrip("/") for u in re.findall(r"https://www\.linkedin\.com/in/[^\"\\\s]+", txt)}
    pay = json.load(open(os.path.join(c.D, "heyreach_leads_payload.json")))
    from urllib.parse import unquote
    norm = lambda u: unquote(u).lower().rstrip("/")
    have_n = {norm(h) for h in have}
    missing = [x for x in pay if norm(x["profileUrl"]) not in have_n]
    print(f"list has {len(have)} leads, payload has {len(pay)}; missing: {len(missing)}")
    for x in missing:
        print(f"  MISSING {x['firstName']} {x['lastName']} {x['profileUrl']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("batch_dir")
    ap.add_argument("command", choices=["enrich", "qualify", "split", "build_fc", "apply_fc", "merge", "check", "diff_list"])
    ap.add_argument("dumps", nargs="*", help="for diff_list: every saved get_leads_from_list page (100 leads per page)")
    ap.add_argument("--prefix", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--writers", type=int, default=10)
    ap.add_argument("--per", type=int, default=15)
    ap.add_argument("--chunk", type=int, default=25)
    ap.add_argument("--include-maybe", action="store_true")
    ap.add_argument("--actor", default="harvestapi~linkedin-profile-scraper")
    ap.add_argument("--max-usd", type=float, default=2.0)
    ap.add_argument("--env", default="")
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args()
    c = Ctx(a)
    if a.command == "diff_list":
        if not a.dumps:
            sys.exit("diff_list needs the path(s) to the list dump pages")
        diff_list(c, a.dumps)
    else:
        if a.command == "enrich":
            enrich(c, a.actor, a.max_usd, a.env, a.yes)
            return
        if a.command == "qualify":
            qualify(c, a.include_maybe)
            return
        {"split": split, "build_fc": build_fc, "apply_fc": apply_fc, "merge": merge, "check": check}[a.command](c)


if __name__ == "__main__":
    main()

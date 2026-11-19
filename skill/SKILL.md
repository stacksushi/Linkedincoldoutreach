---
name: prospect-to-sequence
description: Take a raw prospect list (CSV from Apollo, GetLeads, Sales Nav), qualify it, enrich it with public data, write a personalized sequence per prospect, fact-check every claim, and load it as a DRAFT campaign in HeyReach (LinkedIn) or Smartlead (email). Use when asked to analyze a list, enrich prospects, draft a sequence, or "run the flow" on a new list.
---

# Prospect list to fact-checked sequence

The method behind a set of LinkedIn and email outbound campaigns built around a research-based asset offer. Every step has a gate. Do not skip a gate to save time: the fact-check step alone removed bad claims from roughly 35 to 45 percent of agent-written prospects.

Standing rules (apply to every client): campaigns and drafts only, never start a campaign (the human presses Start). HOLD rows are not pushed unless the human approves. Every claim in a message must be fact-checked before upload. Never ask for, accept, echo or store API keys in chat. Client-specific rules (voice, offer, ICP, authority line, risky topics) come from `client.yaml`.

## Step 0. Client intake (do this first, every new client)

Use a template, not memory. Create the client folder if it does not exist:

```
python3 scripts/new_client.py <client-slug>
```

This makes `clients/<slug>/` with `client.yaml`, a locked `.env` (permissions 600, empty keys) and `batch-1/`. A filled example is `examples/client.example.yaml`. If `client.yaml` is already filled in, show a short summary of it and ask only what is missing or changed.

Then interview the user with AskUserQuestion, one topic at a time, and write the answers into `client.yaml`. Do not guess any of these:

1. **Offer.** What does the prospect get for replying (a report, a teardown, an audit, a sample)? How is it produced from public information, what does it cost the prospect, is there real client proof you may cite? This decides message 3 and the ask. If there is no proof, keep the authority line soft.
2. **Platform.** Which tool sends the LinkedIn sequence: HeyReach (native MCP is connected, Claude can create the list and a DRAFT campaign) or Closely (no connector; Claude produces the send-ready CSV and the user imports it in the Closely UI)? Is there also an email tool (Smartlead, ManyReach)? Which sender account, which schedule and timezone? If HeyReach, confirm the sender account is active.
3. **Risky claims.** Ask what topics could embarrass the sender or hurt the prospect if mentioned even when true (layoffs, lawsuits, funding trouble, personal or health topics, competitor names, politics). Put them in `risky_claims.topics`. The fact-check agents mark these RISKY and they are removed from messages.
4. **Keys.** Tell the user which keys this client needs and where to put them: `clients/<slug>/.env`, edited by the user, never pasted in chat. Needed keys depend on the platform: Apify (post enrichment), HeyReach or Closely, Smartlead, Apollo. Check that each needed line is non-empty without printing the value (for example `grep -c '^APIFY_KEY=.' .env`). If a key is missing, stop at that step and say which one.
5. **ICP and exclusions.** Who is targeted, who is excluded and why, what to dedupe against (fit criteria come next, in item 7).
6. **Voice.** Banned words, tone, length, whether the sender can say "we" or only "I".
7. **Qualification criteria.** Ask what a good-fit candidate looks like and write it to `icp.fit_criteria`: the industry or business model (for example SaaS, compliance, managed services, healthcare practices), industries and business models to exclude, company size, titles that count and titles that never count, minimum time in role, and any past industry that raises confidence. If the user is unsure, offer examples and let them choose; do not invent the criteria.

Read `client.yaml` at the start of every later step. If something in this skill conflicts with `client.yaml`, `client.yaml` wins.

## Script

The mechanical steps are in `scripts/batch_pipeline.py` (run `python3 scripts/batch_pipeline.py -h`). Commands: `enrich` (profile data via Apify, needs `--yes`), `qualify` (verdicts to prospects.json), `split` (writer batches), `build_fc` (fact-check inputs), `apply_fc` (apply corrections, write the claim report), `merge` (sequences CSV, send-ready CSV, 25-lead upload chunks, then lint), `check` (lint only), `diff_list` (compare the upload payload with the saved pages of a HeyReach list). Agents still do the writing and fact-checking; the script does everything around them. For a batch folder that uses a file prefix (batch 3 used `li3_`), pass `--prefix li3_`.

## Working folder

One folder per batch, inside the project, never only in a scratchpad (scratchpads get wiped on context rollover):

```
linkedin-batchN/
  prospects_raw.json      analyzed list (Step 1)
  profiles_raw.json       full profile data (Step 2)
  profiles.json           current role, tenure, history per prospect
  qual_out_*.json         qualification verdicts from agents (Step 3)
  prospects.json          qualified prospects, ranked
  excluded.json, maybe.json, qualification-report.csv
  remaining_pool.json     reserve
  posts.json              recent posts for qualified prospects (Step 4)
  batch_{1..10}.json      writer inputs, 15 each
  out_{1..10}.json        writer outputs (corrected in place later)
  fc_batch_*/fc_out_*     fact-check inputs and verdicts
  pipeline.py             build_fc, apply_fc, merge helpers
  *-sequences.csv         all rows
  *-send-ready.csv        CLEAN + FIX only
  fact-check-report.csv   every claim with verdict
  payload_chunk_*.json    upload payloads, 25 leads max each
```

## Step 1. Analyze the list (CSV only, no spend)

1. Load the CSV. Open with `encoding="utf-8-sig"` (BOM breaks the first header).
2. Profile it before touching it: row count, columns, duplicates by profile URL and by company, title distribution, state distribution, missing profile URLs.
3. Drop rows that are plainly out using only CSV fields: empty or personal-looking profile, titles in `roles_exclude`, companies already contacted in another batch. Record each drop with a reason (`excluded.json`). Do not judge industry fit yet; that needs the profile and company data from Step 2.
4. Rank by seniority and title fit, cap the slice (150 for a batch), and keep the rest in a reserve pool.
5. Dedupe against every earlier batch and against the live HeyReach lists and Smartlead campaigns. Same company with several people is allowed, but the copy must differ per person.

Output: `prospects_raw.json` (analyzed, deduped, ranked, capped) and a one-paragraph summary: in, dropped by reason, kept, reserve.

Gate 1: show the counts and 5 sample prospects. Nothing has been spent yet.

## Step 2. Enrich: profiles and company details

Goal: the facts needed to decide fit. No messages are written yet.

Profile data (work history and current role):
- Run `python3 scripts/batch_pipeline.py <batch_dir> enrich`. It prints the estimate (default actor `harvestapi~linkedin-profile-scraper`, mode `Profile details no email ($4 per 1k)`, about $0.004 per profile) and refuses to spend until you re-run with `--yes`. It passes a `maxTotalChargeUsd` cap (`--max-usd`).
- The token comes from the client `.env` as `APIFY_KEY`. Never echo the key. A 401 means the token is wrong or expired; stop and ask the user to update `.env`.
- Output: `profiles_raw.json` (full) and `profiles.json` (current title, current company, tenure in months, location, last five roles with dates), keyed by the prospect's profile URL. Unmatched profiles are listed; hashed `ACoAA...` URLs may not match by identifier.
- The profile is the source of truth for current company and role. A company shown by HeyReach or in the CSV can be stale.

Company details (public web only):
- Company website, what it sells and to whom, business model, size band, locations, leadership page, news in the last 6 months.
- Tech and vendor footprint: portal, booking tool, EHR named on the site, DNS (SPF, DKIM, DMARC), mail provider. `dig +short TXT _dmarc.domain` is the quick check.
- Never use anything that needs a login. Never infer a vendor from a job posting alone.

Gate 2: state the estimated cost and wait for approval before `enrich --yes`. After the run, report coverage (profiles matched, unmatched) and how many profiles disagree with the CSV on company.

## Step 3. Qualify the candidate

Goal: decide from evidence whether each person and their company fit `icp.fit_criteria`, before spending on posts or writing.

Inputs from Step 2: `profiles.json` (current role, tenure, history) plus the company details. For each prospect check, and cite the source (a profile field or a company page URL):
1. Role fit: title matches `roles_include`, not `roles_exclude`; seniority is right for the offer.
2. Company fit: industry and business model match the includes and avoid the excludes; size is in range; geography is allowed. Do not trust the source list's category column (about half were wrong once); reclassify from the company's own description.
3. Currency: the profile's current company must match the CSV. If it differs, requalify against the new employer.
4. Tenure: time in role versus `min_months_in_role`. Very new in seat can be a reason to reach out or to wait; follow the client's rule.
5. History signal: prior roles in `prior_experience_bonus` industries raise confidence.

Verdict per prospect:
- **FIT:** every must-have is met and each has a source.
- **MAYBE:** a must-have is unknown or borderline. Say which.
- **NO:** any must-have fails. Give a coded reason (`wrong_industry`, `wrong_business_model`, `too_small`, `wrong_role`, `changed_jobs`, `no_evidence`).

Rules: with `require_evidence: true`, never mark FIT from a guess. Run in parallel like the writers (10 agents of 15), each returning JSON with `profile_url`, `verdict`, `reason` (starting with the code), `evidence`, `current_role_confirmed`, `company_type`, `tenure_months`.

Then run `python3 scripts/batch_pipeline.py <batch_dir> qualify`. It writes `prospects.json` (FIT, plus MAYBE only with `--include-maybe`), `excluded.json`, `maybe.json` and `qualification-report.csv`. When `profiles.json` exists it also compares the list company with the profile company: a FIT whose profile company does not match is downgraded to MAYBE with reason `role_check_mismatch`.

Gate 3: show verdict counts, the top exclusion reasons, and about 10 MAYBE rows with the open question for each. The user decides which MAYBEs go in.

## Step 4. Enrich posts (qualified prospects only)

Goal: one true, specific, recent fact per prospect that a human would actually mention. Only qualified prospects are scraped, so nothing is spent on rejected rows.

- Actor `harvestapi~linkedin-profile-posts` on Apify, no cookie, works on hashed `ACoAA...` URLs. Same key and spend rules as Step 2.
- Cost reference: about $0.37 for 42 profiles, $0.78 for 145, $1.10 for 150. Estimate before running and tell the user.
- Expect about 78 percent of profiles to return posts (117 of 150). The rest get company-level personalization only.
- Output: `posts.json`. Report coverage: percent with a recent post, percent with a company fact, failures.

## Step 5. Draft the sequence

LinkedIn sequence (5-step framework: casual opener, controlled compliment, authority statement, curiosity question, ask only after a reply):

| Step | Content | Timing |
|---|---|---|
| Connection request | blank note | withdraw after 14 days |
| Message 1 | `hey {FIRST_NAME}, how's it going` | 3 hours after accept |
| Message 2 | one specific, true compliment from a post or company fact | +1 day |
| Message 3 | `sequence.message3_template` from `client.yaml`. Shape: `bit of context on me - I put together <asset> for <category> from what is visible publicly. <one sentence tied to their setup>. curious, <one concrete question about their stack or process>?` | +2 days |
| Ask | only after they reply. Offer to send the 1-pager here, no call needed | manual |

Rules for the writers:
- Message 2 contains only facts that can be quoted from the post or the website. Prefer the post; when there is none, use a company fact. Never invent a number, a title, a site count, or a date.
- Dates: use the relative term only if you can prove it ("last month" needs a dated post). Stale news is the most common error.
- No "!" and no emojis. Strip apostrophes and quotes from variable fields that go into templates.
- Message 3 question must be answerable in one line and must show up in the category's real stack (EHR, portal, texting vendor, referral intake, imaging transfer, billing clearinghouse).
- Never state a finding the research cannot prove. The asset describes exposure or opportunity from public information, not a verdict. Put banned phrases in `voice.banned_phrases`.
- Two people at one company get different angles and different questions.
- Fallbacks for Message 2 and 3 (when the custom field is empty) may only use `{FIRST_NAME}`, `{LAST_NAME}`, `{MY_FIRST_NAME}`, `{MY_LAST_NAME}`. Use generic role-based lines.

Email sequence (Smartlead or ManyReach):
- No "free", "get", "new", "access", "home", "medical", "urgent", "now" in subjects or bodies (full 33-word list in the spam-word-checker skill).
- Every subject unique per lead. Subject lines that are 100 percent similar across a campaign land in spam. Aim for personal, 2 to 4 words, no regulation or compliance keywords in the subject.
- Spintax with no apostrophes, no commas inside options, no HTML. Expand contractions.
- Warm inboxes first. An unwarmed 5-day-old inbox sent 290 emails and produced zero human replies; the fix was pausing, warming 2 to 3 weeks, rewriting subjects, and removing the sent leads.
- Field limit about 1800 characters per custom field.

Write with parallel agents:
- 10 writer agents, 15 prospects each. Input is the prospect row plus its posts. Output is JSON with message 2, message 3, the source fact used, and the source type (post or company).
- Fan-out of 10 can hit the session rate limit (429). If agents die, resume the same agent with SendMessage; do not relaunch.

## Step 6. Fact-check (the gate that matters)

A second set of 10 agents reviews every claim adversarially. They have no stake in the draft.

For each prospect:
1. Split the message into claims.
2. Mark each claim VERIFIED, UNVERIFIED, WRONG, or RISKY (true but could embarrass: a layoff, a lawsuit, a personal topic).
3. Run the ICP gate: does the person still work there, in a role that handles PHI or vendor risk? Is the company a covered entity or business associate?
4. Verdict: CLEAN (send), FIX (provide the corrected line), HOLD (rewrite needed or ICP fails).
5. Confidence: high, medium, low.

Then apply: FIX corrections are written in place, HOLD rewrites are done (or the row is excluded), and the merge produces send-ready (CLEAN + FIX) and holds. Batch 3 result for scale: 150 prospects gave 73 CLEAN, 69 FIX, 8 HOLD, with 93 corrections.

Also check: identical or near-identical messages for people at the same company, any "!", any apostrophe left in a field, any message over the platform's character limit.

Gate 4: user sees counts, the HOLD list with one-line reasons, and a sample of 10 messages before upload.

## Step 7. Load as a DRAFT

HeyReach (via its MCP connector):
1. `create_empty_list` (USER_LIST).
2. `add_leads_to_list_v2`, 100 per call maximum; chunk files of 25 keep payloads readable. Custom fields: `MSG2`, `MSG3`. Pass a non-empty last name (an empty last name is silently dropped; use "-"). URLs with accents may be stored decoded, so compare carefully when counting.
3. `create_campaign` with the sequence tree and the list attached at creation. A draft campaign rejects `add_leads_to_campaign`; leads flow from the list when it is started.
   - CONNECTION_REQUEST node needs `"messages":[""],"fallbackMessage":""` or the API returns HTTP 500.
   - Every MESSAGE node needs a conditional END on reply.
4. `update_campaign_schedule`: Mon to Fri 09:00 to 17:00 America/New_York.
5. Verify with `get_list_by_id` that the list count equals the send-ready count. Diff against the payload if it is short.
6. Never call `start_campaign`. Tell the user: press Start in HeyReach, stagger it against running campaigns (one sender does about 25 connection requests a day), and check the sender account is active.

Smartlead (email): use the `smartlead-campaign-upload-public` skill. API key as `api_key` query param. Tags come back as `{tag_id, tag_name}`. Lead update is `POST /campaigns/{id}/leads/{lead_id}` with `email` required. Throttle bulk deletes and updates (429/520), make scripts resumable.

## Step 8. After launch: replies

- A reply exits the lead from the sequence, so the next message must be sent by hand. Check `get_conversations_v2` and list conversations where `lastMessageSender == "CORRESPONDENT"`.
- Which step they answered decides the next message: answered Message 1, send Message 2; answered Message 2, send Message 3; answered Message 3, send the ask.
- Before sending a stored message, re-check the prospect's current role and any claim in it (this caught an unverified "command center fielding calls" line).
- Stages: Interested -> offer the 1-pager here, no call. Build the asset only after they say yes. Not interested -> one-line close and stop the sequence.

## Checklist (copy per batch)

- [ ] Intake done, `client.yaml` filled (offer, platform, risky topics, fit criteria), keys in `.env`
- [ ] Step 1: counts and 5 samples shown (Gate 1)
- [ ] Step 2: profile cost approved (Gate 2), `enrich --yes` run, unmatched and company mismatches reviewed
- [ ] Step 3: qualification done, MAYBEs decided by the user (Gate 3)
- [ ] Step 4: posts pulled for qualified prospects only, coverage reported
- [ ] Step 5: writers done (10 x 15), rate-limited agents resumed not relaunched
- [ ] Step 6: fact-check done, corrections applied, HOLDs listed
- [ ] Same-company pairs checked for duplicate wording
- [ ] CSVs delivered: send-ready, all sequences, fact-check report
- [ ] Step 7: draft campaign created, list count equals send-ready count (Gate 4 approved first)
- [ ] Reminders given: user starts it, sender active, stagger starts
- [ ] Memory or notes updated with batch state

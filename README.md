# prospect-to-sequence

> Claude Code skill and scripts that turn a raw LinkedIn prospect list into a qualified, fact-checked outreach sequence, loaded as a draft (never auto-started).

A repeatable way to turn a raw prospect list into a fact-checked outbound sequence, loaded as a **draft** campaign in your sending tool. It is a Claude Code skill plus a small Python script that does the mechanical work around it.

The core idea: AI is good at writing personalized openers and bad at knowing which of its claims are true. This repo puts a **fact-check gate** between the writing and the sending. In the campaigns this was built for, the gate removed or corrected claims in roughly 35 to 45 percent of the prospects the writer agents had drafted (wrong titles, outdated news, invented site counts).

## What it does

```
raw CSV -> analyze -> enrich -> qualify -> posts -> draft -> fact-check -> load as DRAFT -> replies
           gate 1    gate 2    gate 3             agents    gate 4 (you approve)      manual next step
```

1. **Analyze**: profile the list, drop plainly-out rows from CSV fields alone, rank, dedupe against earlier batches. No spend.
2. **Enrich**: pull each prospect's current role, tenure and work history (profile scraper, about $4 per 1,000 profiles, approved before it runs) and read the company's own site.
3. **Qualify**: check that data against the fit criteria you gave (for example B2B SaaS, compliance software, healthcare practices). Every criterion needs a source; no source means MAYBE, not FIT. A FIT whose profile company does not match your list is downgraded automatically. You decide the MAYBEs.
4. **Posts**: pull recent posts for the qualified prospects only, so nothing is spent on rejected rows.
5. **Draft**: a 5-step LinkedIn framework (casual opener, specific compliment, soft authority, curiosity question, ask only after a reply), or an email sequence with deliverability rules.
6. **Fact-check**: a second set of agents splits each message into claims and marks them VERIFIED, UNVERIFIED, WRONG or RISKY, then returns CLEAN, FIX (with a corrected line) or HOLD.
7. **Load**: create the list and a draft campaign in HeyReach (via its MCP connector), or produce a send-ready CSV for tools without a connector, such as Closely. The campaign is never started for you.
8. **Replies**: a reply exits the lead from the sequence, so the next message is sent by hand. The skill says which one based on the step they answered, and re-checks the claim before you send.

## Repo layout

```
skill/SKILL.md                 the Claude Code skill (copy into ~/.claude/skills/prospect-to-sequence/)
scripts/batch_pipeline.py      split, build_fc, apply_fc, merge, check, diff_list
scripts/new_client.py          scaffold a client folder
client-template/client.yaml    every client-specific decision, filled in during intake
client-template/.env.example   key names only, no values
examples/client.example.yaml   a fictional filled-in client
```

## Quick start

Requires Python 3.9+ and Claude Code. The script itself uses only the standard library.

```bash
# 1. install the skill
mkdir -p ~/.claude/skills/prospect-to-sequence
cp skill/SKILL.md ~/.claude/skills/prospect-to-sequence/SKILL.md

# 2. create a client
python3 scripts/new_client.py acme-health
#    -> clients/acme-health/{client.yaml, .env (chmod 600), batch-1/}

# 3. add your API keys to clients/acme-health/.env yourself (never paste keys into chat)

# 4. in Claude Code
#    "run the flow for acme-health" and attach your prospect CSV
```

The skill starts with an intake interview and writes your answers into `client.yaml`:

| Question | Why it matters |
|---|---|
| What is the offer? | Message 3 and the ask depend on it. No proof means a soft authority line. |
| Which platform? | HeyReach (Claude builds a draft campaign) or Closely (Claude delivers a CSV you import). Plus any email tool. |
| What is risky to mention? | Layoffs, lawsuits, personal topics. Fact-check agents mark these RISKY and they are removed. |
| Keys | Tells you which keys the platform needs and which `.env` line to fill in. It checks a line is non-empty without printing it. |
| Who is a good fit? | Industry or business model to include (SaaS, compliance, services, healthcare practices), what to exclude, company size, titles that count, minimum time in role. This becomes `icp.fit_criteria`. |
| ICP, exclusions, voice | Drives the ICP gate and tone rules. |

## How to use it, step by step

You talk to Claude Code; the skill drives the steps and stops at each gate. Work inside the client folder, for example `clients/acme-health/batch-1/`.

**0. Prepare your CSV.** Export from Apollo, Sales Navigator, GetLeads or similar. It needs at least a LinkedIn profile URL, first and last name, job title and company. Company website and location help. Put it in the batch folder.

**1. Start the flow.** Say:

> run the flow for acme-health on batch-1/prospects.csv

If `client.yaml` is empty, Claude interviews you first (offer, platform, risky topics, keys, ICP, voice). Answer each question; it writes your answers to `client.yaml`.

**2. Gate 1: analysis.** Claude shows counts (rows in, dropped by reason, kept, reserve) and 5 sample prospects, and writes `prospects_raw.json`. Nothing has been spent. Reply "go" or change a rule.

**3. Gate 2: enrichment cost.** Claude tells you the estimated Apify cost (for 150 prospects, about $0.60 for profiles) and waits. On your yes it runs `enrich --yes`, which writes `profiles.json` (current role, tenure, last five roles). It reports how many profiles matched and how many show a different company than your list, and reads each company's own site.

**4. Gate 3: qualification.** Claude checks each prospect against your fit criteria with a source for every criterion, runs `qualify`, and shows verdict counts, the top exclusion reasons and about 10 MAYBE rows with the open question for each. You decide which MAYBEs stay. Output: `prospects.json`, `excluded.json`, `maybe.json`, `qualification-report.csv`.

**5. Posts.** Claude pulls recent posts for the qualified prospects only and reports coverage (for example, "117 of 150 profiles had posts").

**6. Drafting.** Claude runs `split`, then 10 writer agents of 15 prospects each. Output: `out_1.json` to `out_10.json`. If the session hits a rate limit, say "resume the writers"; it resumes the same agents instead of starting over.

**7. Fact-check.** Claude runs `build_fc`, launches 10 fact-check agents, then runs `apply_fc`. Output: corrections written into the drafts and `fact-check-report.csv`, with every claim marked VERIFIED, UNVERIFIED, WRONG or RISKY.

**8. Gate 4: before upload.** `merge` writes `<name>-sequences.csv` (all rows), `<name>-send-ready.csv` (CLEAN and FIX only), and upload chunks, then lints. Claude shows the counts, the HOLD list with reasons, and 10 sample messages. Read them. Approve, or ask for changes.

**9. Load as a draft.**
- *HeyReach:* Claude creates the list, uploads the chunks, verifies the list count equals the send-ready count, and creates a DRAFT campaign with the sequence and your schedule. You open HeyReach, check the sender account is active, and press Start.
- *Closely or another tool without a connector:* Claude gives you the send-ready CSV to import in that tool's UI.

**10. Replies.** A reply removes the lead from the sequence, so you send the next message by hand. Ask Claude "check who needs a reply and draft replies". It lists leads who wrote last, tells you which message comes next (answered message 1, send 2; answered 2, send 3; answered 3, send the ask), and re-checks the claim against the prospect's current profile before you send.

**What to expect:** a 150-prospect batch has taken us a few hours of wall-clock time, mostly the agents (your run will vary with rate limits). Enrichment costs roughly $1 per 150 profiles with the Apify actor used here. The human time is the three gates.

## The script

```bash
python3 scripts/batch_pipeline.py <batch_dir> enrich --yes   # prospects_raw.json -> profiles.json (prints the cost; refuses without --yes)
python3 scripts/batch_pipeline.py <batch_dir> qualify    # prospects_raw.json + qual_out_*.json -> prospects.json, excluded.json, maybe.json, report
python3 scripts/batch_pipeline.py <batch_dir> split      # prospects.json + posts.json -> writer batches (15 each)
python3 scripts/batch_pipeline.py <batch_dir> build_fc   # writer outputs -> fact-check inputs
python3 scripts/batch_pipeline.py <batch_dir> apply_fc   # apply FIX/HOLD corrections, write the claim report
python3 scripts/batch_pipeline.py <batch_dir> merge      # sequences CSV, send-ready CSV, 25-lead upload chunks, lint
python3 scripts/batch_pipeline.py <batch_dir> check      # lint only
python3 scripts/batch_pipeline.py <batch_dir> diff_list <page1> [page2 ...]   # upload payload vs saved list pages
```

Options: `--prefix` (file prefix), `--name`, `--writers`, `--per`, `--chunk`, `--include-maybe` (for `qualify`), and for `enrich`: `--yes`, `--max-usd`, `--actor`, `--env`. Run with `-h` for the full contract of the writer and fact-check JSON.

`merge` ends with a lint that flags exclamation marks, emoji, empty or over-long messages, quotes in name fields, HOLD rows, and near-duplicate wording between people at the same company. An empty last name is replaced with `-`, because HeyReach silently drops leads without one.

The agents (writers and fact-checkers) are run by Claude Code; the script prepares their inputs and merges their outputs.

## Rules baked into the process

- Drafts only. A human presses Start.
- HOLD rows are not uploaded unless a human approves them.
- Four approval gates: after analysis, before paid enrichment, after qualification, before upload.
- Every claim in a message is fact-checked against a source before it goes out.
- API keys live in a `.env` that the user edits. They are never pasted into chat or printed.

## Limitations

- `qualify` needs the analyzed list saved as `prospects_raw.json`; any prospect without a qualification result is moved to `excluded.json` as `not_qualified_yet`, so rerun it after resuming interrupted agents.
- Work history comes from the Apify actor `harvestapi~linkedin-profile-scraper` by default (no cookies, roughly $0.004 per profile). Check its terms and current pricing, and the legal position of scraping profile data in your region, before relying on it. The `enrich` command refuses to spend without `--yes` and caps spend with `--max-usd`.
- `split` and `build_fc` expect `prospects.json` and `posts.json` keyed by a `profile_url` field. If your enrichment output uses other field names, adapt the script.
- Closely has no connector in this setup. The skill produces the CSV; check its columns against what your Closely import expects.
- The skill text contains lessons from specific HeyReach and Smartlead quirks (for example, a connection-request node must carry `"messages":[""]`). Those APIs change; treat them as hints, not guarantees.
- This is a workflow, not a guarantee of deliverability or reply rates. Warm your inboxes, keep volume low, and follow each platform's terms and the laws that apply to cold outreach in your target regions.

## Privacy

Do not commit prospect data. The `.gitignore` excludes `.env`, `clients/`, CSVs and upload payloads. Before publishing a fork, check that `client.yaml` files, sender IDs and campaign names have not leaked into your history.

## License

Add a license of your choice before publishing (MIT is common for tooling like this).

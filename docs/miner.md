# Herald Miner Guide (Bittensor netuid 69 · finney)

> **Deprecated: registered miner hotkeys no longer earn.** Herald validators do not query miner
> neurons, read miner commitments or weight miner UIDs. They set weight only on the subnet's single
> incentive hotkey and burn the rest to UID 0. `herald-miner` (`python -m herald.miner.cli`) and the
> miner neuron (`neurons/miner.py`) are deprecated: commits, claims and served reveals earn nothing.
> **PR firms and PR professionals now take part through the Herald website**, as described below. No
> wallet, hotkey, registration, server or command line is needed to contribute.

---

## 1. How contributing works

```
  sign in (Google) ──▶ pick a brief ──▶ UPLOAD your text ──▶ publish ──▶ add the LINK
                                        (before publishing)               ──▶ validators verify
                                                                          ──▶ earnings accumulate
                                                                          ──▶ claim to your wallet
```

1. **Sign in** to the Herald website with Google. Sign-in is required to submit.
2. **Pick a brief.** A brief is a topic or campaign. It may have a start and end date and, for a
   client brief, a prepaid reward pool.
3. **Upload your text before publishing.** On the brief's page, upload the text you will publish
   (300 to 40,000 characters). The time of the upload is recorded.
4. **Publish, then add the link.** Once the article is live in the outlet, return to the brief's
   page and add the article's link. That is all you need to do.
5. **Validators verify.** Once a day every validator checks the article on the outlet's own page
   (§2). The brief's page shows the status of each submission.
6. **Earn and claim.** A verified article earns a share of the alpha the subnet's incentive hotkey
   receives (§3). Earnings accumulate on your account even with no wallet connected. Connect a
   wallet on the website when you want to claim them.

Validators use the uploaded text only to verify your article. They never publish, store or log it.

---

## 2. What a validator checks

Checks run in order and stop at the first failure. Every submitted article must pass all of them:

| Check | What it means for you | Result when it fails |
|---|---|---|
| **Brief active** | The brief is still open when validators score the submission. | `brief_not_active` |
| **Outlet listed** | The link's domain is an outlet in the signed **outlet registry**; its **tier** sets the value. | `outlet_not_listed` |
| **Outlet supported** | Validators read the outlet's page themselves, directly or through their fetch provider. | `outlet_not_supported` |
| **Link live** | The article is reachable and not a thin or challenge page. | `url_not_live` |
| **Publication date** | The page states when the article was published. | `publication_date_unverifiable` |
| **Inside the window** | For a brief with an end date: published from 3 days before the start date (00:00 UTC) through the end date (23:59:59 UTC). Always: no more than 21 days before validators score it. | `published_outside_window` |
| **After the upload** | The article was not published before your upload. When the page states its publication time with a time zone, your upload must be at or before that time; when it gives only a date, or a time with no time zone, the article's UTC publication day must be the day of your upload or later. | `published_before_upload` |
| **Uploaded text present** | Most of the text you uploaded (at least 60% of it) appears in the published article. | `draft_mismatch` |
| **Real news** | It is editorial, **not** a paid, sponsored or press-release page. | `paid_not_real_news` |
| **Topic match** | The article is on the brief's topic. | `topic_mismatch` |

Search-index presence is checked last. It does not reject an article; it sets part of its value.

The link must be an `https` address of at most 2,048 characters with no query string once tracking
parameters (such as `utm_*`) are removed. Each article is verified and credited once.

---

## 3. Value, vesting and earnings (current defaults)

An article's value is multiplicative:

```
value = BASE_PAYOUT ($500) × tier × search
```

- **Tier multiplier** — Tier 1 = `1.0`, Tier 2 = `0.6`, Tier 3 = `0.2`.
- **Search** — found in the search index = `1.0`; not found = `0.5`.

**Vesting and persistence:** the value releases over **30 daily installments (about 30 days)**.
Each installment needs the article to still be **live**. A temporary fetch failure holds the
installment and it is released later. If the article is confirmed removed, or changed to paid
content, on 2 consecutive daily checks, the remaining installments are forfeited.

**Reward pools:** a client brief pays from its prepaid reward pool. Once the pool is spent, its
articles earn nothing more. Standing briefs pay their full installments.

**From value to alpha:** each day validators give the incentive hotkey a share of the subnet's
miner emission equal to the day's verified installments divided by the USD value of that day's
miner emission, capped at 100%. The rest is burned. The alpha the incentive hotkey receives is
shared among contributors by the value of their verified articles, accumulates on each account,
and is claimed to the wallet connected on the website.

> These are consensus parameters set by the subnet operator and can change; treat the numbers as
> current defaults, not guarantees.

---

## 4. Rules & common pitfalls

- **Upload before you publish.** An article published before your upload is rejected: to the second
  when the page states its publication time with a time zone, otherwise by UTC day.
- **Publish what you uploaded.** Editing is expected, but most of the uploaded text must appear in
  the published article.
- **Listed outlets only.** Off-registry domains earn nothing. Higher tiers are worth more.
- **Real editorial only.** Paid, sponsored and advertorial pages fail the real-news check.
- **Stay on topic** and **inside the brief window**.
- **Add the link promptly.** An article published more than 21 days before validators score it is
  rejected.
- **Keep it live for the full vesting window (about 30 days).** A take-down forfeits the remaining
  installments.

---

## 5. If you ran a Herald miner

- The miner neuron, the `herald-miner` commands (`briefs`, `commit`, `claim`, `resubmit`, `list`,
  `pull-reveals`) and `claims.json` no longer affect rewards, and the neuron can be stopped.
- Registered miner hotkeys receive no weight. Bittensor's subnet registration burn is not refunded.
- Vesting entries that started from on-chain claims are expired by validators and pay nothing
  further.
- To keep placing articles, use the Herald website (§1).

See also: [validator.md](validator.md) for how validators verify submissions.

# Herald Miner Guide (Bittensor netuid 69 · finney)

> **PR firms and PR professionals take part through the Herald website, with their own hotkey.**
> Each contributor registers their own hotkey on netuid 69 from the website and signs every
> submission with their wallet; validators weight that hotkey by the verified value of its
> articles. No miner server, axon or command line is needed. `herald-miner`
> (`python -m herald.miner.cli`) and the miner neuron (`neurons/miner.py`) are deprecated:
> validators do not query miner neurons or read miner commitments, so commits, claims and served
> reveals earn nothing.

---

## 1. How contributing works

```
  sign in (Google) ──▶ register your hotkey (once, with your wallet extension)
  pick a brief ──▶ UPLOAD your text + SIGN ──▶ publish ──▶ add the LINK
                   (before publishing)                     ──▶ validators verify
                                                           ──▶ your hotkey earns
```

1. **Sign in** to the Herald website with Google. Sign-in is required to submit.
2. **Register your hotkey (once).** From the website, register your own hotkey on netuid 69,
   signing with a wallet extension: Talisman, SubWallet or Polkadot.js. The account that signs is
   your coldkey, which owns the hotkey on chain. Registration costs the subnet's registration fee
   in TAO, as on any Bittensor subnet, and the fee is not refunded.
3. **Pick a brief.** A brief is a topic or campaign. It may have a start and end date and, for a
   client brief, a prepaid reward pool.
4. **Upload your text before publishing, and sign.** On the brief's page, upload the text you will
   publish (300 to 40,000 characters) and sign the submission with your wallet, using the coldkey
   that owns your hotkey. The signature covers the subnet, the brief, a fingerprint (SHA-256) of
   your text and your hotkey, so the submission can only be credited to your hotkey. The time of
   the upload is recorded.
5. **Publish, then add the link.** Once the article is live in the outlet, return to the brief's
   page and add the article's link.
6. **Validators verify.** Once a day every validator checks your signature, that your coldkey owns
   the hotkey on chain, and the article on the outlet's own page (§2). The brief's page shows the
   status of each submission.
7. **Earn.** A verified article vests on your hotkey (§3). Validators set weight on your hotkey's
   UID, and the chain emits your share to your hotkey directly, as for any Bittensor miner.

Validators use the uploaded text only to verify your article. They never publish, store or log it.

---

## 2. What a validator checks

Checks run in order and stop at the first failure. Every submitted article must pass all of them:

| Check | What it means for you | Result when it fails |
|---|---|---|
| **Signed by your hotkey's owner** | Your wallet's signature is valid, and the coldkey that signed owns your hotkey on chain when validators score the submission. | `hotkey_not_owned`; a submission whose signature does not verify is dropped with no result |
| **Brief active** | The brief is still open when validators score the submission. | `brief_not_active` |
| **Outlet listed** | The link's domain is an outlet in the signed **outlet registry**; its **tier** sets the value. | `outlet_not_listed` |
| **Outlet supported** | Validators read the outlet's page themselves, directly or through their fetch provider. | `outlet_not_supported` |
| **Link live** | The article is reachable and not a thin or challenge page. | `url_not_live` |
| **Publication date** | The page states when the article was published. | `publication_date_unverifiable` |
| **Inside the window** | For a brief with an end date: published from 3 days before the start date (00:00 UTC) through the end date (23:59:59 UTC); an article that gives only a date counts on the date the outlet states. Always: no more than 21 days before validators score it. | `published_outside_window` |
| **After the upload** | The article was not published before your upload. When the page states its publication time with a time zone, your upload must be at or before that time; when it gives only a date, a time with no time zone, or exactly midnight (how many sites show a date alone), the date the outlet states must be the UTC day of your upload or later. | `published_before_upload` |
| **Uploaded text present** | Most of the text you uploaded (at least 60% of it) appears in the published article. | `draft_mismatch` |
| **Real news** | It is editorial, **not** a paid, sponsored or press-release page. | `paid_not_real_news` |
| **Topic match** | The article is on the brief's topic. | `topic_mismatch` |

Search-index presence is checked last. It does not reject an article; it sets part of its value.

The link must be an `https` address of at most 2,048 characters with no query string once tracking
parameters (such as `utm_*`) are removed. Each article is credited once. If several contributors
submit the same article, validators check the submissions in the order their texts were uploaded
and credit the first that passes every check: the earliest matching upload wins.

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

**Keep your hotkey registered.** Installments are paid to your hotkey's UID. If the hotkey is
deregistered, its live articles hold, and the missed installments are released together once it is
registered again. An article still vesting more than 60 days after it started (30 installments
plus 30 days' grace) expires with whatever it has not released.

**Reward pools:** a client brief pays from its prepaid reward pool. Once the pool is spent, its
articles earn nothing more. Standing briefs pay their full installments.

**From value to alpha:** each day validators give your hotkey's UID a share of the subnet's miner
emission equal to your payable installments divided by the USD value of that day's miner emission.
What verified value does not cover goes to UID 0 and is burned. When all contributors' installments
together exceed the day's emission, they share all of it in proportion to their installments.

> These are consensus parameters set by the subnet operator and can change; treat the numbers as
> current defaults, not guarantees.

---

## 4. Rules & common pitfalls

- **Sign with the wallet that owns your hotkey.** A submission signed by any other account is
  rejected.
- **Upload before you publish.** An article published before your upload is rejected: to the second
  when the page states its publication time with a time zone (other than exactly midnight),
  otherwise by the date the outlet states against the UTC day of your upload.
- **Publish what you uploaded.** Editing is expected, but most of the uploaded text must appear in
  the published article.
- **Listed outlets only.** Off-registry domains earn nothing. Higher tiers are worth more.
- **Real editorial only.** Paid, sponsored and advertorial pages fail the real-news check.
- **Stay on topic** and **inside the brief window**.
- **Add the link promptly.** An article published more than 21 days before validators score it is
  rejected, and once an article is credited to a contributor it is not checked again.
- **Keep it live for the full vesting window (about 30 days).** A take-down forfeits the remaining
  installments.
- **Keep your hotkey registered.** While it is deregistered nothing is released; the missed
  installments catch up once it registers again, within the limit in §3.

---

## 5. If you ran a Herald miner

- The miner neuron, the `herald-miner` commands (`briefs`, `commit`, `claim`, `resubmit`, `list`,
  `pull-reveals`) and `claims.json` no longer affect rewards, and the neuron can be stopped.
- A hotkey earns only through submissions signed on the website by the coldkey that owns it.
- Vesting entries that started from on-chain claims are expired by validators and pay nothing
  further.
- To keep placing articles, use the Herald website (§1).

See also: [validator.md](validator.md) for how validators verify submissions.

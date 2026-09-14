# Roadmap — what's missed, what's next, and the honest problems

Written 2026-09-14 after a full check-over of the codebase, for the user and
for the other chats (portfolio, product) that need the same picture. Read
`project-summary.md` first for what exists; this is about what should.

The user's stated direction, in their order: Apple-standard UI → App Store
(for notifications) → any uOttawa student, then anyone on Brightspace →
drop the Pi → charge for it → find users. Each is examined below, and the
recommended order is different from that list for reasons given.

---

## 1. The correction that reorders everything

**Notifications do not need the App Store.** iOS has delivered push
notifications to home-screen web apps since 16.4 (March 2023). The app is
already a home-screen web app with a manifest. What is missing is a
**secure context** — HTTPS — because the browser will not register a push
subscription over plain `http://100.x.y.z:8000`.

Tailscale solves that on the Pi you already have:

    tailscale serve --bg 8000

gives `https://lucapi.<tailnet>.ts.net` with a real Let's Encrypt
certificate, reverse-proxied to the app, reachable only inside the
tailnet. (Needs MagicDNS and HTTPS certificates enabled once in the
Tailscale admin console.) Then a service worker, a VAPID key pair, a
`push_subscriptions` table, and `update.py` sends a push when a scrape
stores something new. Roughly two evenings.

This is the single highest-value thing left, and it is weeks-to-months
earlier than the App Store route. It also answers the question the App
Store cannot: **are notifications what makes this stick?** Find that out
with one user before building a distribution channel for many.

## 2. Features missed so far

Ranked by what they would have caught or saved, not by how interesting
they are to build.

1. **Push notifications** — above. Nothing tells you a deadline appeared.
2. **A backup of `deadlines.db`.** The vault is pushed to git hourly; the
   database — every decision, every dollar of reading, the `text_hash`
   memory that keeps scrapes free — lives only on one USB stick. A stick
   dies without warning. One line in `update.py` copying the DB into the
   `obsidian-brain` repo (it is ~1 MB) makes it recoverable. An evening.
3. **An off-Pi dead-man's switch.** The heartbeat is on the Pi. A Pi that
   is off serves nothing, and the first sign is a deadline that never
   appeared. Free services exist for exactly this: the scrape pings a URL
   at the end of each run, and if the URL goes quiet the service emails.
   Fifteen minutes.
4. **Grades.** Brightspace has a grades endpoint. A posted grade is the
   other thing a student wants to know the moment it happens, and it is
   the same shape of work as announcements. Also a natural push.
5. **Triage is the bottleneck, not extraction.** ~57 dated items sat
   waiting for days. Every downstream feature — To-do, start dates,
   calendar — is only as good as the triage that feeds it. Options: accept
   a whole course at once; a "swipe through everything new" mode; or a
   nudge when the waiting count crosses a threshold.
6. **A Sunday-night digest.** "Here is your week: two things to start,
   one exam, one lab report due Thursday." Email, no HTTPS needed, one
   cron line. Half the value of notifications for a tenth of the work,
   and a good stopgap while push is being built.
7. **Lecture transcription into the vault.** A deadline said aloud and
   never posted is invisible to everything here. Whisper on the new laptop
   is fast; the transcript becomes a vault note and is read like any
   document. This is the one genuinely new *source* of deadlines.
8. **Search the vault by meaning.** ~90,000 words across five courses,
   findable only by remembered wording. A small embedding model on the Pi
   fixes this for free and cannot fabricate — it returns the user's own
   text.
9. **Calendar-fit planning.** The timetable is accurate. "Tuesday
   10:00–14:30 is free; that is where Lab 1 goes." Builds on start dates.
10. **Settings in the app.** Lead times, sections, year typos all live in
    `me.json` and need SSH to change. A settings screen is table stakes
    for anyone but the author.
11. **The three GNG2101 pre-labs that fail to download** (`HTTP 400`).
    `probe_download.py` is waiting on the Pi. Pre-labs are exactly the
    documents that say "install this before Tuesday."

## 3. UI to an Apple standard

The bar named is "the TP-Link app": nothing looks generated, nothing is
fiddly, motion is physical. Two honest routes:

**Polish the web app.** The `Fluid Deadlines` prototype already has the
physics; the app already has the spring engine, the Agenda sheet and the
card exits. What is missing is the *system*: one type scale, one spacing
scale, real empty states, an onboarding screen, a settings screen,
consistent dark mode, and — the one thing web apps get wrong most —
never showing a spinner where content could be. This gets ~80% of the
way for a fraction of the cost and stays maintainable by one person.

**Rewrite in SwiftUI.** The other 20%: real haptics, true 120 Hz gesture
tracking, system share sheets, widgets, Live Activities on the lock
screen ("Lab report due in 3h"). That last one is genuinely compelling for
this product. But it is a second codebase, in a language the author does
not know, and the Pi's Python backend stays regardless.

**Recommendation:** polish the web app first, to the standard of the
prototype, and measure whether the remaining gap bothers *you* in daily
use. Go native only if it does, and only after §5 below is decided —
because the native decision and the multi-user decision are the same
decision (see the on-device architecture).

## 4. The App Store

Needed for **distribution**, not notifications. Two routes:

- **Capacitor** wraps the existing web app in a native shell. Days, not
  months. App Store review is stricter about "just a website" apps than
  it used to be — it has to feel native and offer something a browser
  cannot (push, offline, a widget). Doable.
- **SwiftUI** — see §3.

Prerequisites either way: an Apple Developer account ($99/yr), a privacy
policy (mandatory, and non-trivial given §5), and App Store review will
ask *how the app accesses Brightspace* — see the terms question below.

## 5. Any uOttawa student, then any Brightspace student

This is the fork in the road, and it is architectural, not cosmetic.

### The credential problem

Today the Pi holds `session.json` — sign-on cookies that are a bearer
token for the whole uOttawa account. For one person on their own hardware
that is acceptable. **For anyone else it is not.** A server holding other
students' university credentials is a liability the author should not
want: a breach exposes every user's email, grades, and everything else
behind that SSO. Under PIPEDA it is personal information with a duty of
care attached.

There are two architectures:

**A. Server-side (the Pi, scaled up).** Users log in through the app; the
server stores their session and scrapes for them. Simple to build, keeps
the hourly cadence, and is exactly the liability above. Also the version
most likely to get the app blocked: one IP making thousands of requests
to Brightspace on behalf of many accounts is easy to spot and looks like
an attack.

**B. On-device.** The phone *is* the Pi. The app logs in with a real
browser (WebView), keeps the session on the phone, scrapes from the
phone, and calls the Claude API directly with the app's key (or, better,
through a thin proxy that holds the key and does nothing else). No server
ever sees a credential. Each user's traffic comes from their own phone,
from their own IP, indistinguishable from them opening Brightspace.

The cost of B: iOS does not let an app run hourly in the background.
`BGAppRefreshTask` fires a few times a day at the system's discretion,
not on a schedule. So the scrape runs when the app opens, plus
opportunistically in the background — good enough for "you'll know by
the time you'd act on it," not for "within the hour." Notifications
would then come from the server-side *proxy* noticing something — or,
honestly, from the phone's own background refresh. It is a different
product rhythm from what the Pi provides, and it is the honest one for
other people's accounts.

**Recommendation: B.** It is the only version that can be put in front
of strangers with a straight face, and it is also the version that drops
the Pi — which the user wanted anyway. The Pi stays as the author's own
always-on instance; the product does not depend on it.

### The terms question

Automated access to Brightspace sits in a grey area of uOttawa's
acceptable-use policy and D2L's terms. For one student reading their own
courses politely, nobody cares. For a distributed app, two things reduce
risk: architecture B (every request is the student's own device with
their own session — it *is* them), and rate discipline (sequential
requests, honest User-Agent, nothing faster than a human). The
alternative — D2L's official OAuth integration API — needs the
institution to register the app, which means talking to uOttawa's
teaching-and-learning support unit. Worth one email, later, with usage
numbers in hand; it is also the route to the university *paying* for it
(§6).

### Other Brightspace schools

Every institution has its own SSO chain. The Brightspace API is the same
everywhere; the login dance is not. With architecture B this matters
less — a WebView login works with any SSO because a real browser is doing
it — but the session-renewal trick (`/d2l/login` → SAML) would need
verifying per institution. Do uOttawa first, properly.

## 6. Money

Be honest about the number: this costs the author **$2.79 per term** in
API spend. That is the cost floor per user, plus the proxy. Students are
the most price-sensitive market there is, and the free tier has to be the
deadlines themselves or nobody installs it.

**Freemium shape that could work:**
- Free: deadlines Brightspace states outright (`exact.py`), the list, the
  calendar. No AI cost, so genuinely free to serve.
- Paid (~$3/month or $12/term): the AI reading of documents — the thing
  that finds deadlines buried in a syllabus — plus the vault, prep skills,
  and start dates.

**The B2B angle is stronger than B2C here.** The author's own
psychoeducational report recommended accessibility services. Universities
already pay for accommodation software, and "deadlines extracted from
every course document, surfaced as one list" is an accommodation for
executive-function and attention conditions in a way a lawyer could
write down. uOttawa's accessibility office is a customer who pays once
for everyone, does not need marketing, and legitimises the Brightspace
access question in the same conversation. This is the pitch to make
once there is a term of real use and a handful of users behind it.

## 7. Finding users

Nobody finds an app. The channels that work for a student product at one
university, in order:

1. **One demo that lands.** The moment this is sold is "it found a
   deadline you didn't know you had." Screen-record that on a real
   course. Thirty seconds, no voiceover needed.
2. **Frosh and the first two weeks.** The only time every student is
   looking for tools. Miss it and wait a year.
3. **The engineering student society and course Discords.** One post in
   the right server does more than a month of posters.
4. **Accessibility services** — §6. A referral from them is worth more
   than any ad and costs nothing.
5. **Friends first.** Five people using it for a term produces the
   evidence (misses, fabrications, what people actually tap) that every
   decision above needs. Do this before any of 1–4.

## 8. Recommended order

Different from the user's list, for the reasons above:

| # | What | Why now | Size |
|---|------|---------|------|
| 1 | DB backup + off-Pi heartbeat | The two ways to lose everything, both an evening | S |
| 2 | Push notifications via `tailscale serve` | Highest value, no App Store needed, tests the core hypothesis | M |
| 3 | Triage flow + settings screen | The bottleneck feeding everything else; and no `me.json` for anyone else | M |
| 4 | UI to the prototype's standard | Now, while it is one page and one user | L |
| 5 | Use it for a full term; log every miss and every fabrication | The evidence everything after depends on | — |
| 6 | Five friends on it | Same | — |
| 7 | Decide architecture B, build the on-device version | The fork; also drops the Pi | XL |
| 8 | App Store via Capacitor | Distribution, once there is something to distribute | M |
| 9 | Accessibility-services pitch | With a term of data and users in hand | — |
| 10 | Pricing | Last, and only with usage numbers | — |

The first three are the author's own product getting better this term.
Everything from 7 on is a different project — a company, more or less —
and should be started with evidence rather than enthusiasm.

## 9. What I would not do

- **Not the App Store before notifications work on the PWA.** It is the
  slow route to the thing the fast route gives you this week.
- **Not server-side multi-user.** Holding other people's university
  credentials is the one decision here that cannot be undone by a
  refactor.
- **Not a local LLM for extraction.** Covered elsewhere: the risk is
  invention, not compute, and the saving is ~$0.
- **Not pricing before five users.** The number will be wrong and the
  wrongness will be expensive.

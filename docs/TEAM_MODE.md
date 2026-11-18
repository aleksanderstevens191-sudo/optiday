# OptiDay for teams: a consent-first design

Status: **design only, not built.** This note captures how a company version should work
before any code is written, so the personal product never gets bent into spyware.

## The principle

The employee owns their data. The company gets **aggregates the employee chose to share**,
never a feed of their screen. That isn't only ethics: covert monitoring tools get uninstalled,
gamed, and blocked by works councils, and they're illegal or heavily restricted in much of the
world (GDPR and EU works-council rules, Quebec Law 25, New York and Connecticut notice laws,
California CPRA). A tool people *want* to keep running is the only kind that produces honest
data.

## What stays the same

Each person runs the normal OptiDay on their own Mac. Recording, OCR, the encrypted database,
the local AI, reports, drafts and agent hand-offs all stay on that Mac, under that person's
control. Pause, private apps, exclusions and "delete this day" keep working exactly as today.

## What a team adds

1. **A team server the company hosts** (their own VM, same as `connect-vm` today). It receives
   only what members publish (below) and serves a team dashboard. It never receives screenshots,
   screen text, window titles, URLs or emails.
2. **Sharing levels, chosen by each person and visible in their menu bar:**

   | Level | What the team server receives |
   |---|---|
   | Off (default) | Nothing. |
   | Totals | Daily hours per *project* and per *category* (Coding, Meetings, Email…), focus minutes, meeting minutes. No apps, no sites, no times of day. |
   | Totals + highlights | The above, plus the "what I got done" bullets **after the person reviews and edits them**. |

   Nothing is ever published automatically the first time: the person sees the exact JSON that
   would be sent and approves the level. After that, a daily "here's what was shared" line shows
   in their report.
3. **Small-group floors.** Team dashboards only show numbers for groups of 5 or more people.
   Individual rows are visible to that individual only, plus anyone they explicitly add (e.g.
   a manager for 1:1s), and that grant is revocable at any time.
4. **No ranking, no surveillance features.** No leaderboards, no "least productive employee",
   no live view, no screenshots on request, no keystroke counts, no idle alerts to managers.
   These aren't toggles an admin can turn on; they don't exist in the code.

## What the team actually gets (and why it's valuable)

- **Where the team's time goes:** e.g. "Engineering spends 31% of its week in meetings, up from
  22% last quarter", "Support spends 6h/person/week copying data between Zendesk and Sheets".
- **Automation opportunities, pooled:** OptiDay already detects repeated tedious work and
  proposes agent tasks. With consent, the *pattern* (not the content) can be shared, e.g.
  "9 people do the same Stripe → Sheets copy loop", so the company can build one automation
  for everyone. This is the strongest selling point: it saves time instead of policing it.
- **Focus health:** median uninterrupted focus time per team per week, meeting load, after-hours
  work trends. Used to fix calendars and processes, not to judge individuals.

## Controls and guarantees

- Admin console can configure the server, invite people and set the project list. It cannot
  change anyone's sharing level, read individual data, or turn on any capture.
- Every published record is signed by the member's device key and stored encrypted at rest.
  Members can export or delete everything they've ever shared, from their own Mac.
- Retention on the team server defaults to 13 months, configurable down, never up past 24.
- Open audit log on the team server of who viewed which aggregate.
- A plain-language notice employees see before installing, and a ready-made DPIA template for
  the company's privacy team.

## Rollout path

1. Personal Pro (now): sell to individuals and founders; prove people keep it running.
2. "Team Pro": the sharing levels and team server above, sold per seat to small teams who
   bring it in bottom-up.
3. Enterprise: SSO, MDM deployment of the app (but never of sharing levels), on-prem team
   server, DPIA pack, SOC 2.

## Open questions

- Pricing: per seat vs. per automation saved.
- Whether highlights should be summarized again server-side (by the company's own model) or
  shown as each person wrote them.
- Windows support, which most enterprise fleets will require.

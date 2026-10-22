# Multi-athlete data collection kit

Templates for extending the study from one person (n-of-1) to a small group of volunteers, for example a cycling team.
They are written so that each participant knows exactly what is collected, keeps control over their data, and is
never asked for a password. This is a template, not legal advice: adapt it to your situation and, for anything beyond a
personal project, ask an ethics committee or data-protection officer.

**Principles**

- Participation is voluntary and can be withdrawn at any time, with deletion of the person's data.
- Participants export their own data from Garmin and send the file; nobody else logs into their account.
- Each participant gets a code (A01, A02, …). The code-to-name list is kept separately from the data.
- Only aggregate results are published. Per-person data is published only with a separate, explicit opt-in.
- Raw files, including GPS tracks, never enter the analysis repository; GPS and activity names are dropped on import.
- A coach or teammate never sees a person's individual data without that person's consent.

---

## 1. Information sheet

**What is this?** A personal data-science project that tests whether nightly heart-rate data from a smartwatch can warn
about an illness a few days before symptoms, and tell it apart from the effect of hard training. Methods and code:
https://github.com/hakan-ay/garmin-training-analytics

**What do I share?** A Garmin data export covering about one year, plus short illness and context notes (see section 4).

**What is used?** Nightly resting heart rate, heart-rate variability, respiration, sleep duration, and per-activity
duration and heart rate. **Not used:** GPS tracks, locations, activity names, account details.

**Where is it kept?** On the researcher's computer, in a folder that is never uploaded. Files are deleted at the end of
the project or immediately when you ask.

**What is published?** Group results only, such as "8 athletes, 14 illnesses, 11 flagged on average 2 days early".
Nothing that identifies you, unless you tick the optional box below.

**Risks.** Health data is sensitive. Even without your name, teammates might recognise patterns such as race dates,
which is why per-person data is not published by default.

## 2. Consent form

Participant code: `A__`  ·  Date: `__________`

- [ ] I have read the information sheet and could ask questions.
- [ ] I agree that my Garmin export and my illness/context notes are used for this project as described.
- [ ] I understand I can withdraw at any time and have my data deleted, without giving a reason.
- [ ] *(Optional)* I agree that my own daily results may be shown on the public dashboard under my code, with dates
      shifted and notes removed, as is done for the author's data.

Signature: `______________________`

## 3. How to export your Garmin data

1. Sign in at https://www.garmin.com/account (Garmin account settings) and open **Data Management → Export Your Data**.
2. Request the export. Garmin emails a download link when it is ready; this can take from a few hours to several days.
3. Download the zip and send it through a private channel agreed with the researcher (not a public link).
4. Do not send your password, and do not share your account.

The first export from a pilot participant is used to check what the file contains before the rest of the group is asked.

## 4. Illness and context log

**Past year (once).** List the illnesses you remember, even mild ones:

| Field | Example |
|---|---|
| First day of symptoms | 2000-01-10 (approximate is fine) |
| Recovered (mostly) | 2000-01-14 |
| What it was | cold, flu, COVID-19, stomach bug, unknown |

**Every week (about 30 seconds).** A short form, for example:

1. Were you ill this week? If yes: first day of symptoms, and what it was.
2. Did any of these happen? Travel or time-zone change · heat · alcohol (roughly how much) · very late night ·
   altitude above 2000 m · race · vaccination · unusual stress (exams, work).
3. Anything else that might affect your sleep or heart rate?

Team calendars (races, training camps) are useful context and can be shared once by the coach.

## 5. What the researcher does with it

- Import each export with GPS and activity names dropped, under the participant code.
- Run the same pipeline per athlete: personal baselines, NightSignal alerts, training load and confounders.
- Report in the format used for the external validation: illnesses detected, days early, false alarms per
  athlete-month, with and without the context log.

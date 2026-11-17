# LIMA Peer Mentor Features

LIMA is the peer-mentor version of the Gigi robot for first-year students in the Luddy Living Learning Center. This guide covers the new face, the gender-neutral voice and speed control, typed conversations, the one-on-one check-in activities, and the web app where Peer Mentors and staff review check-ins.

Everything here is a **prototype for testing**. See [Before using with real students](#before-using-with-real-students).

## Try it on a laptop (no robot)

Open two terminals with the virtual environment active:

```powershell
# Terminal 1: the virtual robot
gigi virtual

# Terminal 2: the LIMA web app, starting check-ins on the virtual robot
gigi lima-user add yourname --role admin      # first time only; asks for a password
gigi lima --virtual
```

- Students: open http://127.0.0.1:5080/student, enter a name, pick a check-in, and choose **Talk** or **Type**.
  - In **Type** mode, chat in the web page.
  - In **Talk** mode, answer in the Virtual Robot Console, which stands in for the microphone.
- Staff: open http://127.0.0.1:5080/ and log in.

You can also run a check-in directly from the Virtual Robot Console's **LIMA check-ins** group. The robot then asks for the student's name by voice.

## The face

`lima` is now the default face for every activity. It has soft mint eyes on a deep plum screen, chosen to contrast with the purple body and keep glare low. It is robot-like rather than human, and expressive without being childlike. It blinks, talks, smiles, and looks around using the same frame layout as the original face.

- Change the design: edit the colors and shapes at the top of `tools/generate_lima_face.py`, then run `python tools/generate_lima_face.py --preview` and look at `data/lima_face_preview.png`.
- Switch back to the original face: set `GIGI_FACE=fuzzy`.

## The voice

| | |
| --- | --- |
| Engine | [Piper](https://github.com/OHF-Voice/piper1-gpl): neural TTS that runs in real time on CPUs like the Orange Pi's RK3588 |
| Default voice | `en_US-sam-medium`, trained on the open **Sam** non-binary voice dataset (Accenture Labs / CereProc), Apache-2.0 |
| Speed | 0.6x to 1.6x. Staff set the default; students set their own per check-in |
| Pitch | Optional shift of -4 to +4 semitones |
| Fallback | If Piper or the voice file is missing, the robot uses its previous voice (Nix TTS) |

Settings are stored in `data/settings/voice.json` on each robot. The robot rereads them before every sentence, so changes from the web app apply immediately. Staff can change them in the web app under **Robot voice**.

**Choosing a voice.** No synthetic voice is heard as gender-neutral by every listener, and pitch alone does not make a voice sound neutral. Pick by listening, ideally with LLC students:

```powershell
python tools/download_voices.py en_US-libritts_r-medium   # optional: 904-speaker voice for more candidates
python tools/voice_audition.py --pitch -1.5
# open data/voice_audition/index.html
```

The audition page plays the same three mentor lines in each candidate voice and shows its median pitch. For reference, Sam measured 151 Hz. The page also shows the exact settings to copy into **Robot voice**.

**Licenses.** The `piper-tts` package is GPL-3.0-or-later; this project is Apache-2.0. It is an optional extra (`pip install -e ".[voice]"`). Check with your instructor before redistributing a robot image that includes it. Voice models have their own licenses (listed on each voice's model card): Sam is Apache-2.0, and LibriTTS-R is CC BY 4.0, which requires attribution.

## Typing instead of talking

Students choose **Talk** or **Type** when they start a check-in and can switch at any time on the chat page. **Mute voice** makes LIMA reply in text only. This supports students who find eye contact or voices uncomfortable. Typed messages are accepted in either mode.

## One-on-one check-ins

Five sample check-ins are in `Assets/mentor/checkins/`:

| Check-in | What it covers |
| --- | --- |
| `first_meeting` | Classes, interests, hobbies, how settled in the student feels, upcoming events |
| `weekly_checkin` | Week rating, classes and hobbies the robot remembers, sleep, a message for the mentor |
| `exam_prep` | Upcoming exams, a simple study plan, tutoring |
| `belonging` | Meeting people, event preferences, connecting with groups |
| `resource_finder` | The student asks a question; LIMA points to a resource or to a person and never guesses |

Every check-in follows the same order:
1. LIMA asks for the student's name, unless they gave it in the web app.
2. It explains what it keeps, who reads it, and when it will tell staff.
3. It asks for consent. If the student says no, nothing is kept.
4. It greets the student, mentioning things it remembers from earlier visits.
5. It runs the check-in script.

Students can say or type **skip** for any question and **stop** to end.

Write a new check-in by copying a JSON file. The step types are documented at the top of `src/gigi/mentor/checkin.py`. Questions with `save_as` set to `classes`, `hobbies`, `major`, `looking_forward`, or `event_preference` become profile suggestions for staff to approve.

All resource, event, and crisis wording is in `Assets/mentor/mentor_config.json`, and **all of it is sample text**. LLC staff must replace it with verified information.

## Safety triage

LIMA checks every student reply (`src/gigi/mentor/safety.py`):

- **Urgent** (for example, self-harm or wanting to die): LIMA stops the script, gives the 988 Suicide & Crisis Lifeline and emergency information, says staff are being notified, and flags the check-in as urgent.
- **Follow-up** (for example, overwhelmed, stopped going to class, eating alone): LIMA acknowledges it, shares a relevant resource, and flags the check-in for the Peer Mentor.
- **Note** (for example, low exam confidence, a question LIMA can't answer): a low-priority flag for mentor follow-up.

This is a keyword screen, not a clinical judgement. It misses indirect language and raises false alarms. **Flags only appear in the web app. Nobody is notified automatically yet**, so an urgent flag waits until someone looks. Sessions need human supervision until a real notification and referral process exists.

## The web app: Peer Mentors and staff

Run on the robot with `gigi lima --host 0.0.0.0`. Roles:

| Role | Can |
| --- | --- |
| mentor | Review check-ins and edit profiles for their assigned students and unassigned students |
| staff | Everything mentors can, for all students; assign mentors; delete profiles; set the robot voice |
| admin | Everything staff can, plus manage accounts and read the audit log |

**Reviewing a check-in:**
1. Open the check-in from **Check-ins**. Urgent and follow-up check-ins are listed first.
2. Read the transcript.
3. Keep, edit, or untick the profile suggestions.
4. Optionally add a follow-up note. Don't quote the student in it.
5. Click **Save to profile & delete transcript**.

The transcript text is then deleted from the robot. The check-in's date, flag, reviewer, and follow-up note are kept.

## Data lifecycle

All data stays on the robot in `data/lima/lima.db` (git-ignored).

| Data | Kept until |
| --- | --- |
| Transcript of a check-in | Reviewed, or 14 days (`retention_days`), whichever comes first. Deleted immediately if the student declines consent |
| Profile (name, classes, hobbies, preferences, staff notes) | Staff delete the profile (for example, when a student asks LIMA to forget them) |
| Check-in metadata (date, check-in, flag level and reason, reviewer, follow-up note) | The profile is deleted |
| Audit log (who viewed or deleted what) | Not deleted automatically |

During check-ins, the robot's general interaction logger is switched off, and audio clips of personalized lines are deleted when the check-in ends.

The robot daemon still prints activity output, including recognized speech, to the system log on the Orange Pi. Limit journald retention on robots used with students.

## Running on the Orange Pi

- `Setup/setup_orangepi.sh` installs Piper and downloads the Sam voice.
- `tools/sync_to_robot.py` copies the LIMA face, the check-ins, and `tools/`, and installs Piper and the voice if they are missing.
- Check-ins appear in the daemon's `list` reply as `available_mentor_checkins`. The mobile app can start one with `run mentor_checkin_weekly_checkin`.
- The robot refuses to start any activity until its motors are calibrated. This is the existing safety lockout.
- The daemon accepts one client at a time. While the mobile app is connected, the web app cannot start a check-in.

None of this has been tested on an Orange Pi yet. On the robot, check that:
1. `python -c "import piper"` works.
2. The voice speaks in real time.
3. The face looks right on the 7-inch screen.
4. The microphone transcribes students well enough for Talk mode.

## Before using with real students

- [ ] IRB approval or determination, plus consent forms that match what LIMA actually stores (see `docs/TESTING_GUIDE.md`)
- [ ] LLC staff replace all sample resources, events, and crisis wording, and review every check-in script
- [ ] A real escalation path for urgent flags (who is notified, how, and how fast), agreed with LLC leadership
- [ ] IU single sign-on (CAS/Shibboleth) instead of local passwords, and HTTPS for the web app
- [ ] FERPA review of where the robot, database, and backups live
- [ ] Student identity: the kiosk trusts the name and username typed in. Decide how students are verified
- [ ] Human supervision for every session until the above are in place

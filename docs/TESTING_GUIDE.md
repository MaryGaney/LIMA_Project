# Testing Guide: From Code to Student Sessions

Test in this order. Each stage catches problems the earlier ones cannot, and each is cheaper than the next. Only the last stage involves students.

| Stage | Where | Who takes part | What it proves |
| --- | --- | --- | --- |
| 1. Unit tests | Laptop | Developer | Logic, parsing, file formats, API routes |
| 2. Virtual robot | Laptop (`gigi virtual`) | Developer and teammates acting as students | Conversation flow, wording, branching, gesture choice |
| 3. Hardware bench check | Robot | Developer | Microphone, speaker, camera, screen, servos, NPU |
| 4. Team dry run | Robot | Teammates acting as students | Real speech recognition, timing, the room set-up |
| 5. Pilot with students | Robot, in the LLC | Consenting participants | How real students respond |

## 1. Unit tests

```powershell
.\.venv\Scripts\Activate.ps1
python -m unittest discover -s tests -p "test_*.py" -v
```

Add a test in `tests/` for every logic change. Mock robot I/O instead of opening devices. Use invented names and invented utterances, never real students' words or recordings.

## 2. Virtual robot

See [VIRTUAL_ROBOT.md](VIRTUAL_ROBOT.md). For each activity or conversation change:

- Run it once on the happy path, with the student answering as expected.
- Run it again with off-script answers: "I don't know", one-word answers, a long rambling answer, silence (**Stay silent**), and an answer to the wrong question.
- For mentor conversations, run every scenario preset and write down what the robot should and should not have said. Compare against the boundaries in the LIMA design research: the robot gives resources and study strategies, it does not give medical or emotional advice, and it hands serious issues to a human.
- Export the transcript and attach it to the pull request or team notes. Transcripts from virtual sessions are synthetic test data.

Wizard-of-Oz mode (`LLM: wizard-of-oz only`) is useful for trying conversation designs before the prompt exists: one teammate plays the robot's "brain" and another plays the student.

## 3. Hardware bench check (robot, no participants)

Deploy, then run the diagnostics on the robot:

```bash
# from your laptop: python tools/deploy.py --host <robot-ip>   (or tools/sync_to_robot.py)
# then on the robot:
gigi verify all                        # or: gigi verify mic speaker camera screen
```

These commands open the real microphone, camera, speaker, and screen. Motor movement is locked until that robot has been calibrated. Do not copy another robot's calibration file. Calibration (`gigi calibrate`) is done only on purpose, on the named robot.

Then run the activity once yourself and listen for speech recognition mistakes. Typed input in the virtual robot is always transcribed perfectly; the microphone is not.

## 4. Team dry run

Set the robot up where the pilot will happen. Teammates play students using the same scenarios as in stage 2. Time the session, note where speech recognition failed, check that the robot is audible in the room, and confirm the facilitator knows how to stop the robot: the stop command in the mobile app, `Ctrl+C` in a terminal-started activity, or `sudo systemctl stop gigi-bluetooth.service` on the robot.

## 5. Pilot sessions with students

Before any session with students:

- **Approval.** Studies with students need IRB approval (or a written determination that it is not required) and the course instructor's sign-off. Follow the approved protocol exactly.
- **Consent.** Get informed consent before recording. Tell participants, before they talk to the robot, what it records, who can see it, and when it would tell a human. The LIMA research found students want to know this first.
- **A human in the room.** A team member supervises every session and can stop the robot. For mentor scenarios, have the escalation contacts the LLC staff gave you (for example CAPS) ready on paper. Do not rely on the robot to handle a student in distress.
- **Data handling.** The robot writes participant data to the robot's local disk:
  - `data/`: per-user session folders with conversation transcripts, emotion and gesture events, and activity variables (`src/gigi/core/logger.py`).
  - `logs/`: activity logs, including what students said.
  - `Resources/emoface*.pkl` and `Resources/speaker_db*.pkl`: face and voice embeddings, if face registration or speaker ID ran.
  These paths are git-ignored. Never commit them, copy them to personal devices, or paste them into an LLM or other online tool. Move them to the storage location your protocol approves, then delete them from the robot as the protocol specifies.
- **LLM privacy.** On the robot the LLM runs locally on the NPU. If you configure a cloud provider (OpenAI or Azure keys in `.env`), what students say is sent to that provider. Do not use a cloud LLM with students unless your protocol and consent form say so.
- **Debrief.** Write up what happened the same day. Use participant IDs, not names.

## Where test results go

- Code changes: describe the stages you ran in the pull request (for example "unit + virtual; not yet tested on hardware").
- Behavior or hardware findings: add them to `BUG_LOG.md` or the team notes.
- Changes to setup, test procedure, or data handling: update this guide and `CHANGELOG.md`.

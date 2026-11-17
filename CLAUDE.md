# Claude Working Guide

## Project at a glance

Gigi is a Python social-robot platform targeting an Orange Pi 5 Pro. The package uses a `src/` layout and requires Python 3.10 or newer. Physical-robot setup is Linux-specific. Workstation development uses the **virtual robot** (`gigi virtual`, see `docs/VIRTUAL_ROBOT.md`).

### Project direction: LIMA, the peer-mentor robot

This repository is being adapted from Gigi, an educational robot for children, into **LIMA**, a robot peer mentor for first-year students in the Luddy Living Learning Center (LLC) at Indiana University (INFO-I 440 design research, fall 2026). Findings from the interviews with Peer Mentors and LLC staff that should shape every change:

- **Scope.** The robot handles low-risk, information-type support: study strategies, campus resources (for example "where can I get mental health services?"), Luddy and LLC events, and routine scheduling or admin that burns out Peer Mentors. Demand peaks during welcome week, NSO, and the first week of classes.
- **Hand-off to humans.** Emotional support and questions without easy answers belong to human Peer Mentors. For serious distress (for example "overwhelmed for weeks, stopped going to class") the robot gives resources (tutoring, CAPS) and the protocol flags the student's Peer Mentor or LLC staff. Short-term stress (exam prep) gets study strategies and a low-priority note for mentor follow-up.
- **Transparency first.** Before a student shares anything, the robot says what it records, when it will notify a person, and exactly what it passes on. FERPA applies. The robot never over-promises and never gives medical or definitive emotional advice.
- **Access to notes.** Only Peer Mentors and LLC staff, authenticated with IU credentials, may see logged notes.
- **Embodiment.** Robot-like, not humanlike (avoid the uncanny valley). Approachable for introverted students. Offer typing as well as talking (some students find eye contact and loud voices uncomfortable).
- **Human in the loop** for review of anything the robot logs or decides.

**What exists (prototype, see `docs/LIMA_MENTOR.md`):**
- the `lima` face (default) and the Piper voice `en_US-sam-medium` with speed and pitch settings
- typed input and mute
- five scripted one-on-one check-ins in `Assets/mentor/checkins/`, with consent, skip/stop, and memory of reviewed profile facts
- keyword safety triage (`mentor/safety.py`)
- the LIMA web app (`gigi lima`): kiosk + mentor/staff review that deletes transcripts after review or retention

**Still open:**
- urgent flags notify nobody automatically
- local passwords instead of IU SSO, and no HTTPS
- the kiosk trusts typed identity
- all resource, event, and crisis text is sample text awaiting LLC staff
- older LLM prompts are still child-oriented: `interaction/manager.py`, `core/conversation.py`, `core/robot.py`, and `interaction/safety_filter.py`

Do not ship child-oriented behavior as mentor behavior. Escalation, identity, and data-handling changes need explicit team and LLC decisions. Keep crisis responses scripted (never LLM-generated) and keep a human reviewing everything the robot logs.

## Repository map

- `src/gigi/core/`: configuration (`config.py`), robot lifecycle (`robot.py`, the `GigiRobot`/`Character` class), daemon (Bluetooth/TCP/WebSocket command server that launches activities as subprocesses), conversation and LLM calls, and session logging.
- `src/gigi/hardware/`: PCA9685 motor controller, calibration, camera, and NPU integration.
- `src/gigi/perception/`: vision, hearing, speech recognition, speaker ID, and pronunciation.
- `src/gigi/expression/`: animated face, speech output, visemes, and motor movement/gestures.
- `src/gigi/interaction/`: lesson planning, strategies, safety filtering, runners (`runner.py` for `activity_plan_*`, `custom_runner.py` for `custom_interaction_*`), LLM providers, and the Flask planner (`web/`).
- `src/gigi/activities/`: reading fluency, Mastermind, Math Quest, Story Game, scripted lessons, and social activities. Each is a standalone script that builds its own `GigiRobot`.
- `src/gigi/mentor/`: LIMA peer-mentor features. `store.py` (SQLite at `data/lima/lima.db`: profiles, transcripts, inbox, flags, staff, audit); `checkin.py` (engine for `Assets/mentor/checkins/*.json`); `safety.py` (triage); `launcher.py` (starts check-ins via the daemon or the virtual console); `robot_io.py`; `portal/` (the `gigi lima` web app). The runner is `activities/mentor/mentor_checkin.py`; the daemon routes `mentor_checkin_<id>` to it.
- `src/gigi/core/voice_settings.py`: robot voice (engine, voice, speed, pitch) in `data/settings/voice.json`, read by `expression/speech.py` before every sentence.
- `src/gigi/virtual/`: virtual robot (stand-in hearing, speech, vision, servos, LLM routing, face mirror), the Virtual Robot Console (`console/`), and the conversation sandbox.
- `src/gigi/verification/`: hardware and AI diagnostics; some commands access real devices.
- `Assets/`: lesson plans (`activity_plan_*`), custom interactions (`custom_interaction_*`), activity content, and media.
- `Resources/`: speech and ML models (Git LFS). Avoid changing or replacing model files unless requested.
- `tests/`: `unittest` tests for logic, local subsystems, Flask routes, and the virtual robot.
- `Setup/` and `tools/`: Orange Pi provisioning, backup, synchronization, and deployment.
- `tools/`: also `generate_lima_face.py` (face frames), `download_voices.py` and `voice_audition.py` (Piper voices).
- `docs/`: `LIMA_MENTOR.md`, `VIRTUAL_ROBOT.md`, `TESTING_GUIDE.md`, diagrams, and the interactive tutorial (`docs/tutorial/gigi_explorer.html`).

## Development and validation

Create and activate a virtual environment, then install the project and development tools:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[dev]"
```

Run the test suite:

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

Run lint when Ruff is installed. The existing code is not clean against the full rule set, so for new or changed code check real errors at minimum and do not add new findings:

```powershell
ruff check --select F,E9,B src/ tests/
```

CI (`.github/workflows/ci.yml`) runs the named test modules on Ubuntu with Python 3.10 and 3.11. Use focused `python -m unittest tests/test_....py` runs while iterating, followed by discovery when practical.

To check behavior end to end without hardware, run `gigi virtual`, start the activity, and drive it from the console. The console has a JSON API (`/api/run`, `/api/events`, `/api/input`, `/api/llm/reply`, `/api/scene`) that scripts can use for repeatable end-to-end checks. Tests and the virtual robot are not a substitute for checking physical sensors, actuators, audio devices, or NPU behavior.

## Runtime and hardware safety

- `gigi` is the CLI entry point (`gigi --help`); `gigi-verify` is the diagnostic entry point.
- `gigi web --host 127.0.0.1 --port 5000` starts the Flask activity planner, and `gigi virtual` starts the Virtual Robot Console on `127.0.0.1:5050`. Keep both bound to localhost for local development (`gigi web` defaults to `0.0.0.0`, so pass `--host`).
- `gigi demo`, `python tools/interactive_session.py`, `gigi verify all`, and several verification commands can open the display, camera, microphone, speakers, or other devices. Read the selected command before running it.
- `GIGI_IS_ROBOT` overrides runtime detection. If running on a Linux workstation for software-only testing, set `GIGI_IS_ROBOT=false` (or use `GIGI_VIRTUAL=1`, which forces it) before starting Python; Linux otherwise defaults to robot mode and may try to open I2C hardware.
- On a non-Linux workstation without virtual mode, `HAS_MOVEMENT` is disabled and the PCA9685 controller's PWM calls are no-ops. In virtual mode, `VirtualMovement` runs the real trajectory code against a recording controller and shows **commanded** joint positions. Neither is a servo or physics simulation. Do not claim a movement was physically validated unless it was observed on hardware.
- Never bypass the calibration lockout or use another robot's calibration. `VIRTUAL_MOTOR_MAP` in `virtual/movement.py` is not a calibration and must never be written to `motorData_calibrated.json`. Do not run calibration or deployment commands without an explicit request and confirmed target robot.
- Keep secrets, participant data, face/voice embeddings, recordings, logs (`logs/`, `data/`), and per-robot calibration out of version control. Do not use real student identities or recordings in development tests; use fictional names in tests, presets, and examples.
- The face renderer only regenerates sprite slices when they are missing or older than their source frame. If `Assets/face/fuzzy/*.png` still show as modified, the pixels are identical; restore them with `git checkout -- Assets/face` rather than committing.
- LIMA data (`data/lima/`, `data/settings/`, `data/speech_cache/`) and Piper models (`Resources/piper/`) are git-ignored. Never commit them. Tests must use a temporary `MentorStore(path)` and fictional names.

## Making and documenting changes

- Follow existing Python style, module boundaries, and activity patterns. Put tests in `tests/` and prefer deterministic unit tests with mocked robot I/O (or the virtual bridge in terminal mode: `VirtualBridge(use_console=False)` plus `set_bridge`) over tests requiring devices or network services.
- A new hardware-facing method used by activities must also exist on the matching `gigi.virtual` stand-in, or virtual runs break.
- For behavior changes, check all affected layers: activity/interaction logic, robot-facing interfaces, the virtual robot, web or mobile/daemon integration as applicable, tests, and user-facing docs.
- For LIMA mentor features, check the change against the direction above (scope, hand-off, transparency, privacy) and say in the summary which of those it touches.
- Explain whether validation is unit, integration, virtual (simulated), or physical. Never describe no-op hardware calls or the virtual joint model as physical validation.
- Update the relevant README or docs when setup, CLI, student-facing behavior, test procedure, data handling, or hardware safety changes.
- Record every change in `CHANGELOG.md` under **Unreleased**: what changed, why, files touched, how it was validated, and known gaps.
- Preserve unrelated working-tree edits. Do not overwrite model binaries, calibration, credentials, or user-generated data. (The Spanish Vosk model files under `Resources/vosk-model-small-es-0.42/` currently show as modified because the working tree holds Git LFS pointer files; run `git lfs pull` rather than committing them.)

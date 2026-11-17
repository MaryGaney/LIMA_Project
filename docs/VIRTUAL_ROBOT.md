# Virtual Robot

The virtual robot lets you run Gigi activities on a laptop with no robot, microphone, camera, servos, or LLM server. It runs the **same activity code** that runs on the Orange Pi; only the hardware-facing subsystems are swapped for software stand-ins that you drive from a web page called the **Virtual Robot Console**.

Use it to check conversation flow, turn-taking, branching, wording, and gestures before you book time on the physical robot.

## Quick start

```powershell
.\.venv\Scripts\Activate.ps1
gigi virtual
```

This opens http://127.0.0.1:5050. Then:

1. Pick an activity from the drop-down (or **Open conversation sandbox**) and press **Run**.
2. Read the robot's lines in the **Conversation** panel. Tick **voice captions in browser** to hear them.
3. When the hint says *the robot is listening*, type what the student says and press Enter. **Stay silent** simulates a student who does not answer.
4. Change who the camera "sees" in the **Room** panel: add or remove people, rename them, move them left or right, and set their emotion or hand gesture.
5. If the LLM is not running, a yellow **Wizard-of-Oz** box appears whenever the robot needs a generated reply. Type the reply the robot should give.
6. **Export transcript** downloads the session as JSON.

Options: `gigi virtual --activity math_quest` starts an activity immediately; `--port`, `--no-browser`. Keep `--host` at `127.0.0.1`.

## What is simulated, and how faithfully

| Subsystem | Virtual stand-in | Real code still used | Not simulated |
| --- | --- | --- | --- |
| Hearing (Whisper / Vosk / VAD) | Typed input from the console | Activity parsing of `hearing.texts` | Recognition errors, accents, noise, partial transcripts, pronunciation scoring |
| Speech (TTS) | Captions with an estimated speaking time (2.6 words/s); optional browser voice | Turn timing, viseme mouth animation | Actual TTS voice and pronunciation (use `real TTS` mode for that) |
| Face display | The **real** renderer, mirrored into the page | All of it | Robot screen size and brightness |
| Vision (camera, face ID, emotion) | A simulated room you edit | Face cache, `last_data` format, `look_for`, offsets | Detection or recognition errors, lighting, occlusion |
| Servos | `VirtualMovement`, which subclasses the real `Movement` | Gesture definitions, interpolation, smoothing, normalized angles | Servo speed, load, backlash, collisions, per-robot calibration |
| LLM | Configured LLM (Ollama) if reachable, otherwise you (Wizard-of-Oz) | Prompts and response cleaning | Model latency on the Orange Pi NPU |
| Speaker ID | Off | | Everything |

The joint view shows **commanded** positions. It is not evidence that a movement is safe or looks right on the physical robot. Always confirm gestures on hardware.

Virtual mode never opens I2C, never loads the robot's calibration file, and does not offer motor calibration in the console.

## Modes and environment variables

`gigi virtual` sets these for each activity it launches. You can also set them yourself to run an activity from a terminal (`$env:GIGI_VIRTUAL="1"; python src/gigi/activities/math_quest/math_quest.py`); without a console, the robot prints its lines and reads replies from the terminal.

| Variable | Default | Meaning |
| --- | --- | --- |
| `GIGI_VIRTUAL` | `false` | `1` turns on the virtual robot. Forces `IS_ROBOT=false` and disables NPU paths. |
| `GIGI_VIRTUAL_CONSOLE` | `http://127.0.0.1:5050` | Console address used by the robot process. |
| `GIGI_VIRTUAL_LLM` | `auto` | `auto`: LLM, falling back to Wizard-of-Oz. `real`: LLM only. `woz`: a human writes every reply. |
| `GIGI_VIRTUAL_SPEECH` | `captions` | `real` uses the real TTS engine (needs its models) and still shows captions. |
| `GIGI_VIRTUAL_SPEECH_RATE` | `2.6` | Words per second used to time captions. |
| `GIGI_VIRTUAL_PATIENT` | `true` | Wait for the tester to type instead of using the robot's listening timeout. Set `false` to test timeouts. |
| `GIGI_VIRTUAL_MAX_WAIT` | `900` | Longest wait, in seconds, for typed input or a wizard reply. |

The console's LLM and Speech drop-downs set `GIGI_VIRTUAL_LLM` and `GIGI_VIRTUAL_SPEECH` for the next run.

## Real TTS

`Speech: real TTS` uses the robot's speech engine instead of captions. On Windows that engine is Silero, which needs PyTorch (not installed by default) and downloads its voice model from GitHub the first time it runs:

```powershell
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
```

If the engine cannot start, the activity falls back to captions and the console shows a ⚠ notice. For hearing a voice during routine testing, **voice captions in browser** is lighter and needs no install. The robot itself uses Nix TTS, so on-robot listening is still the only check of the real voice.

## Using an LLM

`auto` mode tries the LLM the robot code is configured for. On a workstation that is Ollama at `http://localhost:11434` with model `llama3.2:1b-instruct-q4_K_M` (see `src/gigi/core/conversation.py`). Install Ollama and run `ollama pull llama3.2:1b-instruct-q4_K_M` to use it. The transcript marks which robot lines a human wizard wrote.

## Conversation sandbox

**Open conversation sandbox** (`src/gigi/virtual/sandbox_chat.py`) is a minimal greet, listen, reply loop for prototyping a persona, such as the LIMA peer mentor, before writing a full activity. Put a draft system prompt in a text file and set `GIGI_SANDBOX_PROMPT_FILE` to its path before starting `gigi virtual`. The sandbox has no referral, escalation, or data-handling logic. It runs only in virtual mode and is for prompt testing, not for use with students.

## Scenario presets

The Room panel has presets based on the LIMA design-research scenarios (exam stress, dropping a class, eating alone, one-word answers, preferring to type, feeling overwhelmed for weeks). A preset sets the student's emotion and a tester note and pre-fills the student's line. It does not send the line until you press Enter.

## How it works

```
Browser (console.html) ──HTTP──▶ Console server (gigi.virtual.console.app, Flask)
                                      │  launches the activity with GIGI_VIRTUAL=1
                                      ▼
                         Activity process ── GigiRobot (core/robot.py)
                           ├─ VirtualSpeech   ─┐
                           ├─ VirtualHearing  ─┤
                           ├─ VirtualVision   ─┼─ VirtualBridge ──HTTP──▶ console
                           ├─ VirtualMovement ─┤
                           ├─ real Face + face mirror
                           └─ Conversation + virtual LLM routing
```

The switch is made in `src/gigi/core/robot.py` and `src/gigi/interaction/robot_interface.py`, which import the `gigi.virtual` classes when `GIGI_VIRTUAL` is set. Activities need no changes.

## Known limitations

- The real face renderer opens its own desktop window in addition to the mirror in the page.
- LIMA check-ins (the **LIMA check-ins** group) work in the console; start them from the LIMA web app (`gigi lima --virtual`) to test typed input and the staff review flow. See [LIMA_MENTOR.md](LIMA_MENTOR.md).
- Reading Fluency's word-by-word karaoke and pronunciation scoring depend on streaming audio, so they only partly work with typed input.
- Make Friends stores no face encoding in virtual mode. Names are kept in memory and mirrored to the Room panel.

## Tests

`tests/test_virtual_robot.py` covers the virtual subsystems and the console API without a display or devices:

```powershell
python -m unittest tests/test_virtual_robot.py -v
```

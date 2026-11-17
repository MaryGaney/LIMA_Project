"""
LLM routing for virtual runs.

``GIGI_VIRTUAL_LLM`` selects how ``Conversation`` gets the robot's generated replies:

* ``auto`` (default): call the configured LLM (Ollama on a workstation); if it is
  unreachable or errors, ask the console operator to write the reply (Wizard-of-Oz).
* ``real``: always use the configured LLM, exactly like the robot (no fallback).
* ``woz``: always ask the operator. Useful for scripted pilot sessions and for
  testing conversation design before an LLM prompt exists.

Every reply is reported to the console with its source so transcripts show which
lines were model-generated and which were written by a human wizard.
"""

from __future__ import annotations

import os
import types

from gigi.virtual.bridge import get_bridge


def install_virtual_llm(conversation) -> None:
    if conversation is None:
        return
    mode = os.environ.get("GIGI_VIRTUAL_LLM", "auto").lower()
    bridge = get_bridge()
    original_call = conversation._call_npu

    def _ask_wizard(messages):
        reply = bridge.request_wizard_reply(messages)
        bridge.emit("llm", source="wizard", reply=reply)
        return reply or "Okay."

    def virtual_call(self, messages, model=None, raise_on_error=False):
        if mode == "woz":
            return _ask_wizard(messages)
        try:
            reply = original_call(messages, model=model, raise_on_error=True)
            bridge.emit("llm", source="model", reply=reply)
            return reply
        except Exception as e:
            if mode == "real":
                bridge.emit("llm", source="error", reply=f"LLM error: {e}")
                if raise_on_error:
                    raise
                return original_call(messages, model=model, raise_on_error=False)
            print(f"[Virtual] LLM unavailable ({e}); asking the console operator (Wizard-of-Oz).")
            return _ask_wizard(messages)

    conversation._call_npu = types.MethodType(virtual_call, conversation)
    print(f"[Virtual] LLM mode: {mode}")

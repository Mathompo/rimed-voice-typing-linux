#!/usr/bin/env python3
"""IBus input method engine for voice typing.

Receives text commands over a Unix socket from the voice typing STT pipeline
and uses IBus commit_text / update_preedit_text to atomically insert text
into any focused application.

This eliminates the need for uinput/ydotool key injection and works uniformly
across terminals (Ghostty, kitty) and GUI apps (Firefox, editors).

Architecture:
  enhanced-voice-typing.py --[socket]--> ibus_voice_engine.py --[IBus API]--> focused app

Socket protocol (newline-terminated commands):
  preedit:TEXT     - Show TEXT as underlined preedit (streaming partials)
  commit:TEXT      - Clear preedit and atomically commit TEXT
  delete:N         - Delete N characters before cursor
  replace:N:TEXT   - Delete N chars before cursor, then commit TEXT
"""

import os
import sys
import socket
import threading
import atexit

import gi

gi.require_version("IBus", "1.0")
from gi.repository import IBus, GLib

try:
    from rime_client import RimeClient, CANDIDATE_SELECT_KEYS
    RIME_AVAILABLE = True
except ImportError:
    RIME_AVAILABLE = False

RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
IBUS_SOCKET_PATH = os.path.join(RUNTIME_DIR, f"voice-typing-ibus-{os.getuid()}.sock")
IBUS_CAPS_PATH = os.path.join(RUNTIME_DIR, f"voice-typing-ibus-caps-{os.getuid()}")

# Global reference to the active engine instance (set by factory)
_active_engine = None


class VoiceTypingEngine(IBus.Engine):
    """IBus engine that receives voice typing text over a socket.

    Embeds a Rime session so Chinese (双拼) and voice/English input can
    coexist in a single IBus engine.  Press Shift to toggle modes:
      - English/Voice mode: keyboard passes through, STT socket active
      - Chinese mode: keyboard fed to embedded Rime, candidates shown inline
    """

    __gtype_name__ = "VoiceTypingEngine"

    # IBus modifier flag for Shift
    _SHIFT_MASK  = 1
    # IBus release-mask bit (bit 30)
    _RELEASE_MASK = 1 << 30
    # X11 keysyms for left/right Shift
    _SHIFT_KEYSYMS = (0xFFE1, 0xFFE2)

    # Special key keysym → rime key-name map
    _SPECIAL = {
        0xFF08: "BackSpace",
        0xFF0D: "Return",
        0xFF1B: "Escape",
        0x0020: "space",
        0xFF52: "Up",
        0xFF54: "Down",
        0xFF51: "Left",
        0xFF53: "Right",
        0xFF55: "Page_Up",
        0xFF56: "Page_Down",
        0xFF50: "Home",
        0xFF57: "End",
        0xFFFF: "Delete",
        0xFF09: "Tab",
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._enabled = False
        self._surrounding = False
        # Chinese mode state
        self._rime_mode = False
        self._shift_pressed = False   # track bare Shift press for toggle
        self._rime = None
        if RIME_AVAILABLE:
            # Schema / dirs configurable via env:
            #   RIME_SCHEMA      e.g. export RIME_SCHEMA=double_pinyin
            #   RIME_USER_DIR    e.g. export RIME_USER_DIR=~/.config/fcitx/rime
            #   RIME_SHARED_DIR  (rarely needed)
            self._rime = RimeClient()
            if not self._rime.start():
                self._rime = None
                print("[engine] librime init failed — Chinese mode disabled")
            else:
                schema = self._rime.SCHEMA_ID.decode()
                print(f"[engine] embedded rime ready — schema={schema} (Shift to toggle 中/EN)")
                print(f"[engine] to change schema: export RIME_SCHEMA=<schema_id> before launching")

    # ── IBus lifecycle ────────────────────────────────────────────────────────

    def do_enable(self):
        self._enabled = True
        print("IBus VoiceTyping engine enabled")

    def do_disable(self):
        self._enabled = False
        # Do NOT call _hide_preedit() here — connection may already be NULL
        print("IBus VoiceTyping engine disabled")

    def do_focus_in(self):
        self._write_caps()

    def do_focus_out(self):
        # Abandon any in-progress rime composition on focus loss
        if self._rime and self._rime_mode:
            self._rime.clear()
        # Do NOT call _hide_preedit() — connection may be gone

    def do_reset(self):
        if self._rime and self._rime_mode:
            self._rime.clear()
        self._hide_preedit()

    def do_set_capabilities(self, caps):
        self._surrounding = bool(caps & IBus.Capabilite.SURROUNDING_TEXT)
        self._write_caps()
        print(f"Client caps: surrounding_text={self._surrounding} (0x{caps:x})")

    # ── key routing ───────────────────────────────────────────────────────────

    def do_process_key_event(self, keyval, keycode, state):
        """Route keys: bare Shift toggles Chinese mode; Chinese mode feeds Rime."""
        is_release = bool(state & self._RELEASE_MASK)

        # Track bare Shift presses for mode toggle
        if keyval in self._SHIFT_KEYSYMS:
            if not is_release:
                self._shift_pressed = True
            else:
                if self._shift_pressed:
                    # Shift pressed and released with no other key → toggle
                    self._shift_pressed = False
                    self._toggle_rime_mode()
                    return True   # consume the Shift
            return False

        # Any non-Shift key cancels the bare-Shift toggle
        self._shift_pressed = False

        if is_release:
            # Pass releases through in both modes
            return False

        if not self._rime_mode or self._rime is None:
            # English / voice mode: pass everything through unchanged
            return False

        key_str = self._keyval_to_str(keyval, state)
        if key_str is None:
            return False

        modifiers = self._SHIFT_MASK if (state & self._SHIFT_MASK) else 0
        result = self._rime.process_key(key_str, modifiers)

        # Update preedit
        if result.preedit:
            self._show_rime_preedit(result.preedit, result.candidates)
        else:
            self._hide_preedit()

        # Commit text produced by Rime
        if result.committed:
            try:
                self.commit_text(IBus.Text.new_from_string(result.committed))
            except Exception as e:
                print(f"[engine] rime commit error: {e}")

        return result.consumed

    # ── Rime helpers ──────────────────────────────────────────────────────────

    def _toggle_rime_mode(self):
        self._rime_mode = not self._rime_mode
        if not self._rime_mode:
            if self._rime:
                self._rime.clear()
            self._hide_preedit()
        label = "中文" if self._rime_mode else "EN/Voice"
        print(f"[engine] mode → {label}")

    def _keyval_to_str(self, keyval, state):
        """Convert IBus keyval to Rime-compatible key strings."""
        # Use IBus built-in helper to get the name
        name = IBus.keyval_name(keyval)
        
        # Handle space explicitly
        if keyval == IBus.KEY_space:
            return "space"
        if keyval == IBus.KEY_Return:
            return "Return"
        if keyval == IBus.KEY_BackSpace:
            return "BackSpace"
        if keyval == IBus.KEY_Escape:
            return "Escape"
            
        # For alphabetic keys, Rime expects simple lowercase letters
        # unless Shift is held.
        if len(name) == 1 and 'A' <= name <= 'Z':
            return name.lower()
            
        return name

    def _show_rime_preedit(self, preedit: str, candidates: list):
        """Display Rime composition + candidate list as IBus preedit."""
        if not self._enabled:
            return
        try:
            if candidates:
                cand_str = "  " + " ".join(
                    f"{i+1}.{c}" for i, c in enumerate(candidates[:5])
                )
                display = preedit + cand_str
            else:
                display = preedit
            ibus_text = IBus.Text.new_from_string(display)
            # Underline only the pinyin part
            ibus_text.append_attribute(
                IBus.AttrType.UNDERLINE, IBus.AttrUnderline.SINGLE, 0, len(preedit)
            )
            self.update_preedit_text_with_mode(
                ibus_text, len(preedit), True, IBus.PreeditFocusMode.CLEAR
            )
        except Exception as e:
            print(f"[engine] rime preedit error: {e}")

    # ── STT socket interface (called from GLib idle) ──────────────────────────

    def commit(self, text):
        """Commit STT text — only in English/voice mode."""
        if not self._enabled or self._rime_mode:
            return
        try:
            self.commit_text(IBus.Text.new_from_string(text))
        except Exception as e:
            print(f"[engine] commit skipped ({e})")

    def preedit(self, text):
        """Show STT preedit — only in English/voice mode."""
        if not self._enabled or self._rime_mode:
            return
        if not text:
            self._hide_preedit()
            return
        try:
            ibus_text = IBus.Text.new_from_string(text)
            if not self._surrounding:
                ibus_text.append_attribute(
                    IBus.AttrType.UNDERLINE, IBus.AttrUnderline.SINGLE, 0, len(text)
                )
            self.update_preedit_text_with_mode(
                ibus_text, len(text), True, IBus.PreeditFocusMode.CLEAR
            )
        except Exception as e:
            print(f"[engine] preedit skipped ({e})")

    def delete_chars(self, count):
        if not self._enabled or self._rime_mode:
            return
        if count > 0:
            try:
                self.delete_surrounding_text(-count, count)
            except Exception as e:
                print(f"[engine] delete_chars skipped ({e})")

    def replace_chars(self, count, text):
        if not self._enabled or self._rime_mode:
            return
        try:
            if count > 0:
                self.delete_surrounding_text(-count, count)
            if text:
                self.commit_text(IBus.Text.new_from_string(text))
        except Exception as e:
            print(f"[engine] replace_chars skipped ({e})")

    # ── internal ──────────────────────────────────────────────────────────────

    def _write_caps(self):
        try:
            with open(IBUS_CAPS_PATH, "w") as f:
                f.write("surrounding\n" if self._surrounding else "basic\n")
        except OSError:
            pass

    def _hide_preedit(self):
        if not self._enabled:
            return
        try:
            self.hide_preedit_text()
        except Exception:
            pass


class VoiceTypingEngineFactory(IBus.Factory):
    """Factory that creates VoiceTypingEngine instances on demand from IBus."""

    __gtype_name__ = "VoiceTypingEngineFactory"

    _engine_count = 0

    def __init__(self, bus):
        self._bus = bus
        super().__init__(object_path=IBus.PATH_FACTORY, connection=bus.get_connection())

    def do_create_engine(self, engine_name):
        global _active_engine
        VoiceTypingEngineFactory._engine_count += 1
        obj_path = (
            f"/org/freedesktop/IBus/Engine/{VoiceTypingEngineFactory._engine_count}"
        )
        engine = VoiceTypingEngine(
            engine_name=engine_name,
            object_path=obj_path,
            connection=self._bus.get_connection(),
        )
        _active_engine = engine
        print(f"Created engine: {engine_name} at {obj_path}")
        return engine


def _handle_socket_command(line):
    """Parse and dispatch a socket command to the IBus engine via GLib main thread."""
    global _active_engine

    if _active_engine is None:
        return

    # Fast-path: drop STT output when engine is disabled or in Chinese mode
    if not _active_engine._enabled or _active_engine._rime_mode:
        return

    if ":" not in line:
        return

    cmd, _, payload = line.partition(":")
    cmd = cmd.strip()

    if cmd == "preedit":
        GLib.idle_add(_active_engine.preedit, payload)
    elif cmd == "commit":
        GLib.idle_add(_active_engine.preedit, "")
        GLib.idle_add(_active_engine.commit, payload)
    elif cmd == "delete":
        try:
            count = int(payload.strip())
            GLib.idle_add(_active_engine.delete_chars, count)
        except ValueError:
            pass
    elif cmd == "replace":
        # Format: replace:N:new text
        parts = payload.split(":", 1)
        if len(parts) == 2:
            try:
                count = int(parts[0])
                text = parts[1]
                GLib.idle_add(_active_engine.replace_chars, count, text)
            except ValueError:
                pass


def _handle_client(conn):
    """Handle a persistent client connection, reading newline-terminated commands."""
    buf = b""
    try:
        while True:
            data = conn.recv(4096)
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line_str = line.decode("utf-8", errors="replace").strip()
                if line_str:
                    _handle_socket_command(line_str)
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _socket_listener():
    """Accept connections from voice typing STT pipeline."""
    if os.path.exists(IBUS_SOCKET_PATH):
        os.remove(IBUS_SOCKET_PATH)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old_umask = os.umask(0o077)
    try:
        server.bind(IBUS_SOCKET_PATH)
    finally:
        os.umask(old_umask)
    os.chmod(IBUS_SOCKET_PATH, 0o600)
    server.listen(2)

    print(f"IBus socket listening: {IBUS_SOCKET_PATH}")

    while True:
        try:
            conn, _ = server.accept()
            client_thread = threading.Thread(
                target=_handle_client, args=(conn,), daemon=True
            )
            client_thread.start()
        except Exception as e:
            print(f"Socket accept error: {e}")
            continue


def _cleanup():
    for path in (IBUS_SOCKET_PATH, IBUS_CAPS_PATH):
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


def main():
    IBus.init()

    bus = IBus.Bus()
    if not bus.is_connected():
        print("Cannot connect to IBus daemon")
        sys.exit(1)

    component = IBus.Component.new(
        "org.freedesktop.IBus.VoiceTyping",
        "Voice Typing Input Method",
        "1.0",
        "MIT",
        "Voice Typing",
        "",
        "",
        "voice-typing",
    )

    engine_desc = IBus.EngineDesc.new(
        "voice-typing",
        "Voice Typing",
        "Voice-to-text input via streaming STT",
        "en",
        "MIT",
        "Voice Typing",
        "audio-input-microphone",
        "us",
    )
    component.add_engine(engine_desc)

    factory = VoiceTypingEngineFactory(bus)
    rc = bus.register_component(component)
    print(f"register_component: {rc}")
    rn = bus.request_name("org.freedesktop.IBus.VoiceTyping", 0)
    print(f"request_name: {rn}")

    # Try to verify the engine is listed
    engines = bus.list_engines()
    found = [e.get_name() for e in engines if "voice" in e.get_name().lower()]
    print(f"Engines with 'voice': {found}")

    # Start socket listener in background thread
    socket_thread = threading.Thread(
        target=_socket_listener, daemon=True, name="IBusSocketListener"
    )
    socket_thread.start()

    print("IBus Voice Typing engine running")
    print(f"Socket: {IBUS_SOCKET_PATH}")

    atexit.register(_cleanup)

    loop = GLib.MainLoop()
    try:
        loop.run()
    except KeyboardInterrupt:
        print("\nShutting down IBus Voice Typing engine")
    finally:
        _cleanup()


if __name__ == "__main__":
    main()

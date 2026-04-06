"""
rime_client.py — ctypes wrapper around librime.so.1

Calls librime via the rime_get_api() function pointer table (RimeApi struct),
which is the only stable C interface this version of librime exposes.

Usage:
    rime = RimeClient()
    rime.start()

    result = rime.process_key("s")
    result.committed   # text committed by rime, may be ""
    result.preedit     # current composition string
    result.candidates  # list of candidate strings

    rime.stop()
"""

import ctypes
import os
import threading
from dataclasses import dataclass, field

# ── C structures ──────────────────────────────────────────────────────────────

class RimeTraits(ctypes.Structure):
    _fields_ = [
        ("data_size",               ctypes.c_int),
        ("shared_data_dir",         ctypes.c_char_p),
        ("user_data_dir",           ctypes.c_char_p),
        ("distribution_name",       ctypes.c_char_p),
        ("distribution_code_name",  ctypes.c_char_p),
        ("distribution_version",    ctypes.c_char_p),
        ("app_name",                ctypes.c_char_p),
        ("modules",                 ctypes.c_void_p),
        ("min_log_level",           ctypes.c_int),
        ("log_dir",                 ctypes.c_char_p),
        ("prebuilt_data_dir",       ctypes.c_char_p),
        ("staging_dir",             ctypes.c_char_p),
    ]

class RimeComposition(ctypes.Structure):
    _fields_ = [
        ("length",      ctypes.c_int),
        ("cursor_pos",  ctypes.c_int),
        ("sel_start",   ctypes.c_int),
        ("sel_end",     ctypes.c_int),
        ("preedit",     ctypes.c_char_p),
    ]

class RimeCandidate(ctypes.Structure):
    _fields_ = [
        ("text",     ctypes.c_char_p),
        ("comment",  ctypes.c_char_p),
        ("reserved", ctypes.c_void_p),
    ]

class RimeMenu(ctypes.Structure):
    _fields_ = [
        ("page_size",                   ctypes.c_int),
        ("page_no",                     ctypes.c_int),
        ("is_last_page",                ctypes.c_int),
        ("highlighted_candidate_index", ctypes.c_int),
        ("num_candidates",              ctypes.c_int),
        ("candidates",                  ctypes.POINTER(RimeCandidate)),
        ("select_keys",                 ctypes.c_char_p),
    ]

class RimeCommit(ctypes.Structure):
    _fields_ = [
        ("data_size",   ctypes.c_int),
        ("text",        ctypes.c_char_p),
    ]

class RimeContext(ctypes.Structure):
    _fields_ = [
        ("data_size",           ctypes.c_int),
        ("composition",         RimeComposition),
        ("menu",                RimeMenu),
        ("commit_text_preview", ctypes.c_char_p),
        ("select_labels",       ctypes.c_void_p),
    ]

# ── RimeApi function pointer table ────────────────────────────────────────────
# Field order must match rime_api.h exactly.

FP = ctypes.c_void_p  # generic function pointer placeholder

class RimeApi(ctypes.Structure):
    _fields_ = [
        ("data_size",                        FP),
        ("setup",                            FP),
        ("set_notification_handler",         FP),
        ("initialize",                       FP),
        ("finalize",                         FP),
        ("start_maintenance",                FP),
        ("is_maintenance_mode",              FP),
        ("join_maintenance_thread",          FP),
        ("deployer_initialize",              FP),
        ("prebuild",                         FP),
        ("deploy",                           FP),
        ("deploy_schema",                    FP),
        ("deploy_config_file",               FP),
        ("sync_user_data",                   FP),
        ("create_session",                   FP),
        ("find_session",                     FP),
        ("destroy_session",                  FP),
        ("cleanup_stale_sessions",           FP),
        ("cleanup_all_sessions",             FP),
        ("process_key",                      FP),
        ("commit_composition",               FP),
        ("clear_composition",                FP),
        ("get_commit",                       FP),
        ("free_commit",                      FP),
        ("get_context",                      FP),
        ("free_context",                     FP),
        ("get_status",                       FP),
        ("free_status",                      FP),
        ("set_option",                       FP),
        ("get_option",                       FP),
        ("set_property",                     FP),
        ("get_property",                     FP),
        ("get_schema_list",                  FP),
        ("free_schema_list",                 FP),
        ("get_current_schema",               FP),
        ("select_schema",                    FP),
        ("schema_open",                      FP),
        ("config_open",                      FP),
        ("config_close",                     FP),
        ("config_get_bool",                  FP),
        ("config_get_int",                   FP),
        ("config_get_double",                FP),
        ("config_get_string",                FP),
        ("config_get_cstring",               FP),
        ("config_update_signature",          FP),
        ("config_begin_map",                 FP),
        ("config_next",                      FP),
        ("config_end",                       FP),
        ("simulate_key_sequence",            FP),
        ("register_module",                  FP),
        ("find_module",                      FP),
        ("run_task",                         FP),
        ("get_shared_data_dir",              FP),
        ("get_user_data_dir",                FP),
        ("get_sync_dir",                     FP),
        ("get_user_id",                      FP),
        ("get_user_data_sync_dir",           FP),
        ("config_init",                      FP),
        ("config_load_string",               FP),
        ("config_set_bool",                  FP),
        ("config_set_int",                   FP),
        ("config_set_double",                FP),
        ("config_set_string",                FP),
        ("config_get_item",                  FP),
        ("config_set_item",                  FP),
        ("config_clear",                     FP),
        ("config_create_list",               FP),
        ("config_create_map",                FP),
        ("config_list_size",                 FP),
        ("config_begin_list",                FP),
        ("get_input",                        FP),
        ("get_caret_pos",                    FP),
        ("select_candidate",                 FP),
        ("get_version",                      FP),
        ("set_caret_pos",                    FP),
        ("select_candidate_on_current_page", FP),
        ("candidate_list_begin",             FP),
        ("candidate_list_next",              FP),
        ("candidate_list_end",               FP),
        ("user_config_open",                 FP),
        ("candidate_list_from_index",        FP),
        ("get_prebuilt_data_dir",            FP),
        ("get_staging_dir",                  FP),
        ("commit_proto",                     FP),
        ("context_proto",                    FP),
        ("status_proto",                     FP),
        ("get_state_label",                  FP),
        ("delete_candidate",                 FP),
        ("delete_candidate_on_current_page", FP),
        ("get_state_label_abbreviated",      FP),
    ]

# ── key definitions ───────────────────────────────────────────────────────────

SPECIAL_KEYS = {
    "BackSpace": 0xFF08,
    "Return":    0xFF0D,
    "Escape":    0xFF1B,
    "space":     0x0020,
    "Up":        0xFF52,
    "Down":      0xFF54,
    "Left":      0xFF51,
    "Right":     0xFF53,
    "Page_Up":   0xFF55,
    "Page_Down": 0xFF56,
    "Home":      0xFF50,
    "End":       0xFF57,
    "Delete":    0xFFFF,
    "Tab":       0xFF09,
}

CANDIDATE_SELECT_KEYS = "1234567890"


@dataclass
class RimeResult:
    committed:  str  = ""
    preedit:    str  = ""
    candidates: list = field(default_factory=list)
    consumed:   bool = True


class RimeClient:
    # Defaults — override via constructor args or environment variables:
    #   RIME_SCHEMA      e.g. "double_pinyin" / "luna_pinyin" / "wubi86"
    #   RIME_USER_DIR    e.g. "~/.config/fcitx/rime" for fcitx users
    #   RIME_SHARED_DIR  rarely needed; defaults to /usr/share/rime-data
    DEFAULT_SHARED_DATA_DIR = b"/usr/share/rime-data"
    DEFAULT_USER_DATA_DIR   = os.path.expanduser("~/.config/ibus/rime").encode()
    DEFAULT_SCHEMA_ID       = b"double_pinyin_mspy"

    def __init__(
        self,
        schema_id: str | None = None,
        user_data_dir: str | None = None,
        shared_data_dir: str | None = None,
    ):
        # Resolve config: constructor arg > env var > compiled-in default
        def _enc(s: str) -> bytes:
            return s.encode()

        self.SCHEMA_ID = _enc(
            schema_id
            or os.environ.get("RIME_SCHEMA", "")
            or self.DEFAULT_SCHEMA_ID.decode()
        )
        self.USER_DATA_DIR = _enc(
            os.path.expanduser(user_data_dir or os.environ.get("RIME_USER_DIR", ""))
        ) or self.DEFAULT_USER_DATA_DIR
        self.SHARED_DATA_DIR = _enc(
            shared_data_dir or os.environ.get("RIME_SHARED_DIR", "")
        ) or self.DEFAULT_SHARED_DATA_DIR

        self._lib     = None
        self._api     = None
        self._session = 0
        self._lock    = threading.Lock()
        self._ready   = False

    def _fn(self, name, restype, *argtypes):
        """Cast a void* field in RimeApi to a typed callable."""
        raw = getattr(self._api.contents, name)
        if not raw:
            raise AttributeError(f"RimeApi.{name} is NULL")
        return ctypes.CFUNCTYPE(restype, *argtypes)(raw)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> bool:
        try:
            self._lib = ctypes.cdll.LoadLibrary("librime.so.1")
        except OSError as e:
            print(f"[rime] cannot load librime.so.1: {e}")
            return False

        self._lib.rime_get_api.restype = ctypes.POINTER(RimeApi)
        self._api = self._lib.rime_get_api()
        if not self._api:
            print("[rime] rime_get_api() returned NULL")
            return False

        try:
            traits = RimeTraits()
            traits.data_size              = ctypes.sizeof(RimeTraits) - ctypes.sizeof(ctypes.c_int)
            traits.shared_data_dir        = self.SHARED_DATA_DIR
            traits.user_data_dir          = self.USER_DATA_DIR
            traits.distribution_name      = b"VoiceTyping"
            traits.distribution_code_name = b"voice-typing"
            traits.distribution_version   = b"1.0"
            traits.app_name               = b"rime.voice-typing"
            traits.min_log_level          = 3

            # Initialize rime core
            self._fn("setup", None, ctypes.POINTER(RimeTraits))(ctypes.byref(traits))
            self._fn("initialize", None, ctypes.POINTER(RimeTraits))(ctypes.byref(traits))

            # --- CRITICAL FIX FOR FEDORA ---
            # Trigger explicit deployment to generate .bin files in user_data_dir
            print("[rime] deploying schema and configuration...")
            self._fn("deploy", None)()
            
            # Start maintenance and wait for the deployment thread to finish
            self._fn("start_maintenance", ctypes.c_int, ctypes.c_int)(0)
            import time
            time.sleep(2.0)  # Give librime some time to compile schemas
            # -------------------------------

            session = self._fn("create_session", ctypes.c_size_t)()
            if not session:
                # If session fails again, try one more time after a short delay
                time.sleep(1.0)
                session = self._fn("create_session", ctypes.c_size_t)()
                if not session:
                    print("[rime] failed to create session after deployment")
                    return False
            
            self._session = session
            self._fn("select_schema", ctypes.c_int,
                     ctypes.c_size_t, ctypes.c_char_p)(self._session, self.SCHEMA_ID)

        except Exception as e:
            print(f"[rime] init error: {e}")
            return False

        self._ready = True
        print(f"[rime] ready — schema={self.SCHEMA_ID.decode()}, session={self._session}")
        return True

    def stop(self):
        if not self._ready:
            return
        self._ready = False
        try:
            if self._session:
                self._fn("destroy_session", ctypes.c_int, ctypes.c_size_t)(self._session)
                self._session = 0
            self._fn("finalize", None)()
        except Exception as e:
            print(f"[rime] stop error: {e}")

    # ── input ─────────────────────────────────────────────────────────────────

    def process_key(self, key: str, modifiers: int = 0) -> RimeResult:
        if not self._ready:
            return RimeResult(consumed=False)
        if key in SPECIAL_KEYS:
            keysym = SPECIAL_KEYS[key]
        elif len(key) == 1:
            keysym = ord(key)
        else:
            return RimeResult(consumed=False)
        with self._lock:
            try:
                consumed = bool(
                    self._fn("process_key", ctypes.c_int,
                             ctypes.c_size_t, ctypes.c_int, ctypes.c_int)(
                        self._session, keysym, modifiers)
                )
                return self._collect(consumed)
            except Exception as e:
                print(f"[rime] process_key error: {e}")
                return RimeResult(consumed=False)

    def clear(self):
        if not self._ready:
            return
        with self._lock:
            try:
                self._fn("clear_composition", None, ctypes.c_size_t)(self._session)
            except Exception as e:
                print(f"[rime] clear error: {e}")

    # ── output ────────────────────────────────────────────────────────────────

    def _collect(self, consumed: bool) -> RimeResult:
        result = RimeResult(consumed=consumed)

        try:
            commit = RimeCommit()
            commit.data_size = ctypes.sizeof(RimeCommit) - ctypes.sizeof(ctypes.c_int)
            gc = self._fn("get_commit", ctypes.c_int,
                          ctypes.c_size_t, ctypes.POINTER(RimeCommit))
            if gc(self._session, ctypes.byref(commit)) and commit.text:
                result.committed = commit.text.decode("utf-8", errors="replace")
                self._fn("free_commit", ctypes.c_int,
                         ctypes.POINTER(RimeCommit))(ctypes.byref(commit))
        except Exception as e:
            print(f"[rime] get_commit error: {e}")

        try:
            ctx = RimeContext()
            ctx.data_size = ctypes.sizeof(RimeContext) - ctypes.sizeof(ctypes.c_int)
            gctx = self._fn("get_context", ctypes.c_int,
                            ctypes.c_size_t, ctypes.POINTER(RimeContext))
            if gctx(self._session, ctypes.byref(ctx)):
                if ctx.composition.preedit:
                    result.preedit = ctx.composition.preedit.decode("utf-8", errors="replace")
                n  = ctx.menu.num_candidates
                ps = ctx.menu.page_size or 5
                if n and ctx.menu.candidates:
                    for i in range(min(n, ps)):
                        try:
                            t = ctx.menu.candidates[i].text
                            if t:
                                result.candidates.append(t.decode("utf-8", errors="replace"))
                        except Exception:
                            pass
                self._fn("free_context", ctypes.c_int,
                         ctypes.POINTER(RimeContext))(ctypes.byref(ctx))
        except Exception as e:
            print(f"[rime] get_context error: {e}")

        return result

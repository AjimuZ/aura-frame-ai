"""Engine run supervision: one child process at a time, with its JSONL events buffered for SSE."""

import json
import os
import re
import subprocess
import threading
from collections import deque
from datetime import datetime, timezone
from itertools import islice
from typing import Any, Iterator

from .errors import ApiError
from .exports import prepare_exports_dir
from .models import RunRequest, RunStatus
from .security import TOKEN_ENV_VAR
from .storage import ApiSettings, library_control_file, library_dir

# Events kept per run. A client that connects late still sees the latest ones; older events are
# dropped first, so memory stays bounded however many photos a run scores.
MAX_BUFFERED_EVENTS = 10000
# A line longer than this is not a progress event and is dropped instead of forwarded.
MAX_EVENT_BYTES = 4096
MAX_MESSAGE_CHARS = 500
KEEPALIVE_SECONDS = 15.0
# After a cancel request the engine is given this long to stop by itself before it is killed.
CANCEL_GRACE_SECONDS = 30.0
JOIN_SECONDS = 5.0

_EVENT_TYPE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


# Returns the environment of the engine process: the parent's, without the API session token,
# which the engine has no use for.
def _child_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key != TOKEN_ENV_VAR}


# Returns the current UTC time as an ISO 8601 string.
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Coerces an event counter to a non-negative int; anything that is not a number counts as 0.
def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


# Returns the status of a library that has no run to report.
def _idle_status(library_id: str) -> RunStatus:
    return RunStatus(
        libraryId=library_id,
        step="idle",
        running=False,
        paused=False,
        current=0,
        total=0,
        message="Idle",
    )


class _Run:
    """State of one engine child process. Every mutation happens under the supervisor lock."""

    def __init__(self, library_id: str, process: "subprocess.Popen[str]") -> None:
        self.library_id = library_id
        self.process = process
        self.status = RunStatus(
            libraryId=library_id,
            step="indexing",
            running=True,
            paused=False,
            current=0,
            total=0,
            message="Starting the analysis.",
            startedAt=_now(),
        )
        self.events: deque[str] = deque(maxlen=MAX_BUFFERED_EVENTS)
        self.base_seq = 0  # sequence number of events[0]
        self.next_seq = 0  # sequence number the next event will get
        self.finished = False  # the process has exited and every event is buffered
        self.cancel_requested = False
        self.saw_terminal = False  # the engine itself emitted done or error
        self.thread: threading.Thread | None = None
        self.timer: threading.Timer | None = None

    # Appends one serialized event, dropping the oldest when the buffer is full.
    def append(self, payload: str) -> None:
        if len(self.events) == self.events.maxlen:
            self.base_seq += 1
        self.events.append(payload)
        self.next_seq += 1


class RunSupervisor:
    """Starts the engine as a child process and exposes its progress to the routes.

    At most one run exists at a time, across all libraries. The command is the fixed
    `ApiSettings.engine_command` plus arguments built here from validated options; the process
    is started without a shell, never reads the API token, and analyzes the mounted photos
    root only. Events are the engine's own JSONL lines, kept when they are well formed JSON
    objects with a `type`, and stripped of the server's root paths.

    Pause, resume and cancel write the engine's `control.json`, so no signal is needed
    except to kill an engine that ignores a cancel for too long.
    """

    def __init__(
        self,
        settings: ApiSettings,
        cancel_grace_seconds: float = CANCEL_GRACE_SECONDS,
        keepalive_seconds: float = KEEPALIVE_SECONDS,
    ) -> None:
        self._settings = settings
        self.cancel_grace_seconds = cancel_grace_seconds
        self.keepalive_seconds = keepalive_seconds
        self._cond = threading.Condition()
        self._run: _Run | None = None

    def start(self, library_id: str, options: RunRequest) -> RunStatus:
        """Start a run for `library_id` and return its initial status.

        Raises ApiError 409 when a run is still active or the photos root is missing, and 503
        when the engine process cannot be started.
        """
        photos_root = self._settings.photos_root
        if not photos_root.is_dir():
            raise ApiError(409, "photos_unavailable", "No photo folder is available to analyze.")
        # Refuses here, before any engine starts, when the exports mount cannot be written.
        exports_dir = prepare_exports_dir(self._settings, library_id)
        data_dir = library_dir(self._settings, library_id)
        args = [*self._settings.engine_command, "analyze"]
        args += ["--input", str(photos_root), "--data-root", str(data_dir)]
        args += ["--exports-root", str(exports_dir)]
        args += ["--preset", options.preset]
        if not options.includeSubfolders:
            args.append("--no-subfolders")
        if options.runGemma:
            args.append("--gemma")
        with self._cond:
            if self._run is not None and not self._run.finished:
                raise ApiError(409, "run_in_progress", "An analysis is already running.")
            # A cancel left behind by an earlier run must not stop this one at once.
            self._write_control(library_id, paused=False, cancel=False)
            try:
                process = subprocess.Popen(
                    args,
                    shell=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    env=_child_env(),
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                )
            except OSError as error:
                raise ApiError(
                    503, "engine_unavailable", "The analysis engine could not be started."
                ) from error
            run = _Run(library_id, process)
            run.thread = threading.Thread(
                target=self._pump, args=(run,), name=f"engine-run-{library_id}", daemon=True
            )
            self._run = run
            run.thread.start()
            return run.status.model_copy()

    def status(self, library_id: str) -> RunStatus:
        """Return the status of the latest run of `library_id`, or an idle status when none."""
        with self._cond:
            run = self._run
            if run is None or run.library_id != library_id:
                return _idle_status(library_id)
            return run.status.model_copy()

    def pause(self, library_id: str) -> RunStatus:
        """Ask the active run of `library_id` to pause at its next checkpoint.

        Raises ApiError 409 when that library has no active run or the run is being cancelled.
        """
        return self._set_paused(library_id, True)

    def resume(self, library_id: str) -> RunStatus:
        """Let a paused run continue. Same refusals as `pause`."""
        return self._set_paused(library_id, False)

    def cancel(self, library_id: str) -> RunStatus:
        """Ask the active run to stop, and kill it if it ignores the request for too long.

        Raises ApiError 409 when `library_id` has no active run.
        """
        with self._cond:
            run = self._active(library_id)
            if not run.cancel_requested:
                self._write_control(library_id, paused=False, cancel=True)
                run.cancel_requested = True
                run.status.paused = False
                run.status.message = "Cancelling the analysis."
                run.timer = threading.Timer(self.cancel_grace_seconds, self._kill, args=(run,))
                run.timer.daemon = True
                run.timer.start()
            return run.status.model_copy()

    def stream(self, library_id: str) -> Iterator[str]:
        """Return an iterator of SSE frames for the latest run of `library_id`.

        The buffered events are replayed first, then new ones follow. The iterator ends once the
        process has exited and every event was delivered. Raises ApiError 404 when that library
        has no run since the server started.
        """
        with self._cond:
            run = self._run
            if run is None or run.library_id != library_id:
                raise ApiError(404, "no_run", "This library has no analysis run yet.")
        return self._frames(run)

    def shutdown(self) -> None:
        """Stop the active engine process, so it never outlives the API server."""
        with self._cond:
            run = self._run
            if run is None or run.finished:
                return
            run.process.terminate()
            thread = run.thread
        if thread is not None:
            thread.join(JOIN_SECONDS)

    # Pauses or resumes the active run through the control file.
    def _set_paused(self, library_id: str, paused: bool) -> RunStatus:
        with self._cond:
            run = self._active(library_id)
            if run.cancel_requested:
                raise ApiError(409, "run_cancelling", "The analysis is being cancelled.")
            self._write_control(library_id, paused=paused, cancel=False)
            run.status.paused = paused
            return run.status.model_copy()

    # Returns the unfinished run of `library_id` or raises 409. The caller holds the lock.
    def _active(self, library_id: str) -> _Run:
        run = self._run
        if run is None or run.finished or run.library_id != library_id:
            raise ApiError(409, "no_active_run", "This library has no running analysis.")
        return run

    # Writes the control file atomically, so the engine never reads a half-written document.
    def _write_control(self, library_id: str, paused: bool, cancel: bool) -> None:
        target = library_control_file(self._settings, library_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + ".tmp")
        temp.write_text(json.dumps({"paused": paused, "cancel": cancel}), encoding="utf-8")
        os.replace(temp, target)

    # Kills an engine that did not honor a cancel within the grace period.
    def _kill(self, run: _Run) -> None:
        with self._cond:
            if not run.finished and run.process.poll() is None:
                run.process.kill()

    # Reads the engine's stdout until it closes, then records how the process ended. Runs on its
    # own thread, so `finished` is always set, even if reading fails.
    def _pump(self, run: _Run) -> None:
        try:
            stream = run.process.stdout
            if stream is not None:
                for line in stream:
                    event = self._parse_event(run, line)
                    if event is not None:
                        with self._cond:
                            self._publish(run, event)
        except (OSError, ValueError):
            pass  # the pipe closed under us (shutdown); the exit code below decides the outcome
        finally:
            code = run.process.wait()
            self._finish(run, code)

    # Turns one stdout line into an event dict, or None when it is not a well formed event.
    # Server root paths are replaced first, so no event reveals where the data lives.
    def _parse_event(self, run: _Run, line: str) -> dict[str, Any] | None:
        text = line.strip()
        for root, label in (
            (self._settings.photos_root, "<photos>"),
            (self._settings.data_root, "<data>"),
            (self._settings.exports_root, "<exports>"),
        ):
            text = text.replace(str(root), label)
        if not text or len(text.encode("utf-8")) > MAX_EVENT_BYTES:
            return None
        try:
            event = json.loads(text)
        except ValueError:
            return None
        kind = event.get("type") if isinstance(event, dict) else None
        if not isinstance(kind, str) or not _EVENT_TYPE.fullmatch(kind):
            return None
        event["libraryId"] = run.library_id
        return event

    # Buffers one event and folds it into the status. The caller holds the lock.
    def _publish(self, run: _Run, event: dict[str, Any]) -> None:
        self._apply(run, event)
        run.append(json.dumps(event, ensure_ascii=False))
        self._cond.notify_all()

    # Updates the status the way the Electron analysis handler does for the same events.
    def _apply(self, run: _Run, event: dict[str, Any]) -> None:
        status = run.status
        kind = event["type"]
        message = str(event.get("message", ""))[:MAX_MESSAGE_CHARS]
        if kind == "progress":
            status.step = str(event.get("step", status.step))[:40]
            status.current = _count(event.get("current"))
            status.total = _count(event.get("total"))
            status.message = message
        elif kind == "local_gate_progress":
            status.step = "local_gate"
            status.current = _count(event.get("current"))
            status.total = _count(event.get("total"))
            status.message = message
        elif kind in ("done", "error"):
            run.saw_terminal = True
            status.step = kind
            status.running = False
            status.paused = False
            status.message = message
            status.finishedAt = _now()

    # Records the end of the process: synthesizes the closing event when the engine emitted none,
    # then marks the run finished so streams can end and a new run may start.
    def _finish(self, run: _Run, code: int) -> None:
        with self._cond:
            if run.timer is not None:
                run.timer.cancel()
            if not run.saw_terminal:
                closing: dict[str, Any]
                if run.cancel_requested:
                    closing = {"type": "log", "level": "info", "message": "Analysis cancelled."}
                elif code != 0:
                    closing = {"type": "error", "message": "The analysis engine stopped unexpectedly."}
                else:
                    closing = {"type": "done", "message": "The analysis finished."}
                self._publish(run, {**closing, "libraryId": run.library_id})
                if run.cancel_requested:
                    run.status.step = "idle"
                    run.status.message = "Analysis cancelled."
            run.status.running = False
            run.status.paused = False
            run.status.finishedAt = run.status.finishedAt or _now()
            run.finished = True
            self._cond.notify_all()

    # Yields SSE frames for `run`: replay, then live events, with a comment frame as keepalive.
    def _frames(self, run: _Run) -> Iterator[str]:
        cursor = 0
        while True:
            keepalive = False
            with self._cond:
                cursor = max(cursor, run.base_seq)
                batch = list(islice(run.events, cursor - run.base_seq, None))
                cursor = run.next_seq
                if not batch:
                    if run.finished:
                        return
                    keepalive = not self._cond.wait(timeout=self.keepalive_seconds)
            if keepalive:
                yield ": keepalive\n\n"
            for payload in batch:
                yield f"data: {payload}\n\n"

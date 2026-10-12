from __future__ import annotations
import logging
import threading
import time
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from bot import Bot
    from bot.player import Player


class SleepTimer:
    """
    Manages a sleep timer to stop music playback after a designated duration.
    """

    def __init__(self, player: Player, bot: Bot):
        self.player = player
        self.bot = bot
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()
        self._start_time: float = 0.0
        self._duration: float = 0.0  # seconds
        self._user_nickname: Optional[str] = None
        self._stop_after_current: bool = False

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._stop_after_current or (self._timer is not None and self._timer.is_alive())

    @property
    def stop_after_current(self) -> bool:
        with self._lock:
            return self._stop_after_current

    def get_remaining_seconds(self) -> int:
        with self._lock:
            if self._stop_after_current:
                # Return remaining seconds of current playing track if available
                try:
                    time_pos = getattr(self.player._player, "time_pos", None)
                    duration = getattr(self.player._player, "duration", None)
                    if time_pos is not None and duration is not None:
                        return max(0, int(duration - time_pos))
                except Exception:
                    pass
                return 0

            if self._timer is None or not self._timer.is_alive():
                return 0
            elapsed = time.time() - self._start_time
            remaining = int(self._duration - elapsed)
            return max(0, remaining)

    def set_stop_after_current(self, user_nickname: Optional[str] = None) -> None:
        """
        Sets the sleep timer to automatically stop after the currently playing track finishes.
        """
        with self._lock:
            self._cancel_unlocked()
            self._stop_after_current = True
            self._user_nickname = user_nickname
        logging.info(
            f"[SleepTimer] Set to stop after current track by {user_nickname or 'Unknown'}."
        )

    def on_track_end(self) -> bool:
        """
        Called when a track finishes playing. If stop_after_current is True, stops playback.
        Returns True if playback was intercepted and stopped.
        """
        with self._lock:
            if not self._stop_after_current:
                return False
            nickname = self._user_nickname
            self._stop_after_current = False
            self._user_nickname = None

        logging.info("[SleepTimer] Current track ended. Stopping playback as requested.")
        threading.Thread(
            target=self._perform_stop,
            args=(nickname,),
            daemon=True,
            name="SleepTimerStopThread",
        ).start()
        return True

    def set_timer(self, minutes: float, user_nickname: Optional[str] = None) -> None:
        """
        Sets a new sleep timer in minutes. Cancels any existing active timer.
        """
        duration = float(minutes) * 60.0
        with self._lock:
            self._cancel_unlocked()
            self._start_time = time.time()
            self._duration = duration
            self._user_nickname = user_nickname

            self._timer = threading.Timer(duration, self._on_timeout)
            self._timer.daemon = True
            self._timer.name = "SleepTimerThread"
            self._timer.start()

        logging.info(
            f"[SleepTimer] Timer set for {minutes:.1f} minutes ({duration:.0f} seconds) by {user_nickname or 'Unknown'}."
        )

    def cancel(self) -> bool:
        """
        Cancels the active sleep timer. Returns True if a timer was active, False otherwise.
        """
        with self._lock:
            if self._stop_after_current or (self._timer is not None and self._timer.is_alive()):
                self._cancel_unlocked()
                logging.info("[SleepTimer] Timer cancelled.")
                return True
            return False

    def _cancel_unlocked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._start_time = 0.0
        self._duration = 0.0
        self._user_nickname = None
        self._stop_after_current = False

    def _on_timeout(self) -> None:
        logging.info("[SleepTimer] Sleep timer expired. Stopping playback...")
        with self._lock:
            self._timer = None
            nickname = self._user_nickname
            self._user_nickname = None
            self._start_time = 0.0
            self._duration = 0.0

        # Execute stop smoothly in a separate background thread if needed
        threading.Thread(
            target=self._perform_stop,
            args=(nickname,),
            daemon=True,
            name="SleepTimerStopThread",
        ).start()

    def _perform_stop(self, nickname: Optional[str]) -> None:
        try:
            from bot.player.enums import State

            if self.player.state != State.Stopped:
                # If volume fading is enabled, fade down gracefully before stopping
                if getattr(self.player.config, "volume_fading", False):
                    try:
                        current_vol = int(getattr(self.player._player, "volume", self.player.volume))
                        fade_interval = getattr(self.player.config, "volume_fading_interval", 0.02)
                        for vol in range(current_vol, 0, -2):
                            self.player._player.volume = vol
                            time.sleep(fade_interval)
                    except Exception as e:
                        logging.debug(f"[SleepTimer] Volume fade failed: {e}")

                self.player.stop()

                # Restore previous volume setting for next playback
                try:
                    self.player._player.volume = self.player.volume
                except Exception:
                    pass

                # Send notification to channel or log
                try:
                    msg = self.bot.translator.translate("Sleep timer expired. Playback stopped.")
                    if self.bot.config.general.send_channel_messages:
                        self.bot.ttclient.send_message(msg, type=2)
                    logging.info(f"[SleepTimer] {msg}")
                except Exception as e:
                    logging.warning(f"[SleepTimer] Failed to send notification: {e}")
            else:
                logging.info("[SleepTimer] Expired, but player is already stopped.")
        except Exception as e:
            logging.error(f"[SleepTimer] Error during timeout execution: {e}")
